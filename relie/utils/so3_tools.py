import math
import torch
from relie.utils.numerical import batch_trace, zero_one_outer_product


def so3_hat(v):
    """
    Map a point in R^N to the tangent space at the identity, i.e.
    to the Lie Algebra. Inverse of so3_vee.

    :param v: Lie algebra in vector rep of shape (..., 3
    :return: Lie algebar in matrix rep of shape (..., 3, 3)
    """
    assert v.shape[-1] == 3

    e_x = v.new_tensor([[0., 0., 0.], [0., 0., -1.], [0., 1., 0.]])

    e_y = v.new_tensor([[0., 0., 1.], [0., 0., 0.], [-1., 0., 0.]])

    e_z = v.new_tensor([[0., -1., 0.], [1., 0., 0.], [0., 0., 0.]])

    x = e_x * v[..., 0, None, None] + \
        e_y * v[..., 1, None, None] + \
        e_z * v[..., 2, None, None]
    return x


def so3_vee(x):
    """
    Map Lie algebra in ordinary (3, 3) matrix rep to vector.
    Inverse of so3_hat
    :param x: Lie algebar in matrix rep of shape (..., 3, 3)
    :return:  Lie algebra in vector rep of shape (..., 3
    """
    assert x.shape[-2:] == (3, 3)
    return torch.stack((-x[..., 1, 2], x[..., 0, 2], -x[..., 0, 1]), -1)


def so3_exp(v):
    """
    Exponential map of SO(3) with Rordigues formula.
    :param v: algebra vector of shape (..., 3)
    :return: group element of shape (..., 3, 3)
    """
    assert v.dtype == torch.double
    theta = v.norm(p=2, dim=-1, keepdim=True)
    # k = so3_hat(v / theta)

    v_normed = v / theta
    v_normed[theta[..., 0] < 1E-20] = 0
    k = so3_hat(v_normed)

    eye = torch.eye(3, device=v.device, dtype=v.dtype)
    r = eye + torch.sin(theta)[..., None]*k \
        + (1. - torch.cos(theta))[..., None]*(k@k)
    return r


def so3_log(r):
    """
    Logarithm map of SO(3).
    :param r: group element of shape (..., 3, 3)
    :return: Algebra element in matrix basis of shape (..., 3, 3)

    Uses https://en.wikipedia.org/wiki/Rotation_group_SO(3)#Logarithm_map
    """
    assert r.dtype == torch.double
    anti_sym = .5 * (r - r.transpose(-1, -2))
    cos_theta = .5 * (batch_trace(r)[..., None, None] - 1)
    cos_theta = cos_theta.clamp(-1, 1)  # Ensure we get a correct angle
    theta = torch.acos(cos_theta)
    ratio = theta / torch.sin(theta)

    # x/sin(x) -> 1 + x^2/6 as x->0
    mask = (theta[..., 0, 0] < 1E-20).nonzero()
    ratio[mask] = 1 + theta[mask] ** 2 / 6

    log = ratio * anti_sym

    # Separately handle theta close to pi
    mask = (cos_theta[..., 0, 0].abs() > 1-1E-5).nonzero()
    if mask.numel():
        log[mask[:, 0]] = so3_log_pi(r[mask[:, 0]], theta[mask[:, 0]])

    return log


def so3_log_pi(r, theta):
    """
    Logarithm map of SO(3) for cases with theta close to pi.
    Note: inaccurate for theta around 0.
    :param r: group element of shape (..., 3, 3)
    :param theta: rotation angle
    :return: Algebra element in matrix basis of shape (..., 3, 3)
    """
    sym = .5 * (r + r.transpose(-1, -2))
    eye = torch.eye(3, device=r.device, dtype=r.dtype).expand_as(sym)
    z = theta ** 2 / (1 - torch.cos(theta)) * (sym - eye)

    q_1 = z[..., 0, 0]
    q_2 = z[..., 1, 1]
    q_3 = z[..., 2, 2]
    x_1 = torch.sqrt((q_1 - q_2 - q_3) / 2)
    x_2 = torch.sqrt((-q_1 + q_2 - q_3) / 2)
    x_3 = torch.sqrt((-q_1 - q_2 + q_3) / 2)
    x = torch.stack([x_1, x_2, x_3], -1)

    # We know components up to a sign, search for correct one
    signs = zero_one_outer_product(3, dtype=x.dtype, device=x.device) * 2 - 1
    x_stack = signs.view(8, *[1]*(x.dim()-1), 3) * x[None]
    with torch.no_grad():
        r_stack = so3_exp(x_stack)
        diff = (r[None]-r_stack).pow(2).sum(-1).sum(-1)
        selector = torch.argmin(diff, dim=0)
    x = x_stack[selector, torch.arange(len(selector))]

    return so3_hat(x)


def so3_xset(x, k_max):
    """
    Return set of x's that have same image as exp(x).
    :param x: Tensor of shape (..., 3) of algebra elements.
    :param k_max: int. Number of 2pi shifts in either direction
    :return: Tensor of shape (2 * k_max+1, ..., 3)
    """
    x = x[None]
    x_norm = x.norm(dim=-1, keepdim=True)
    shape = [-1, *[1]*(x.dim()-1)]
    k_range = torch.arange(-k_max, k_max+1, dtype=x.dtype, device=x.device).view(shape)
    return x / x_norm * (x_norm + 2 * math.pi * k_range)


def so3_log_abs_det_jacobian(x):
    """
    Return element wise log abs det jacobian of exponential map
    :param x: Algebra tensor of shape (..., 3)
    :return: Tensor of shape (..., 3)
    """
    x_norm = x.double().norm(dim=-1)
    j = torch.log(2 - 2 * torch.cos(x_norm)) - torch.log(x_norm ** 2)
    return j.to(x.dtype)


def so3_matrix_to_quaternions(r):
    """
    Map batch of SO(3) matrices to quaternions.
    :param r: Batch of SO(3) matrices of shape (..., 3, 3)
    :return: Quaternions of shape (..., 4)
    """
    batch_dims = r.shape[:-2]
    assert list(r.shape[-2:]) == [3, 3], 'Input must be 3x3 matrices'
    r = r.view(-1, 3, 3)
    n = r.shape[0]

    diags = [r[:, 0, 0], r[:, 1, 1], r[:, 2, 2]]
    denom_pre = torch.stack([
        1 + diags[0] - diags[1] - diags[2],
        1 - diags[0] + diags[1] - diags[2],
        1 - diags[0] - diags[1] + diags[2],
        1 + diags[0] + diags[1] + diags[2]
    ], 1)
    denom = 0.5 * torch.sqrt(1E-6 + torch.abs(denom_pre))

    case0 = torch.stack([
        denom[:, 0],
        (r[:, 0, 1] + r[:, 1, 0]) / (4 * denom[:, 0]),
        (r[:, 0, 2] + r[:, 2, 0]) / (4 * denom[:, 0]),
        (r[:, 1, 2] - r[:, 2, 1]) / (4 * denom[:, 0])
    ], 1)
    case1 = torch.stack([
        (r[:, 0, 1] + r[:, 1, 0]) / (4 * denom[:, 1]),
        denom[:, 1],
        (r[:, 1, 2] + r[:, 2, 1]) / (4 * denom[:, 1]),
        (r[:, 2, 0] - r[:, 0, 2]) / (4 * denom[:, 1])
    ], 1)
    case2 = torch.stack([
        (r[:, 0, 2] + r[:, 2, 0]) / (4 * denom[:, 2]),
        (r[:, 1, 2] + r[:, 2, 1]) / (4 * denom[:, 2]),
        denom[:, 2],
        (r[:, 0, 1] - r[:, 1, 0]) / (4 * denom[:, 2])
    ], 1)
    case3 = torch.stack([
        (r[:, 1, 2] - r[:, 2, 1]) / (4 * denom[:, 3]),
        (r[:, 2, 0] - r[:, 0, 2]) / (4 * denom[:, 3]),
        (r[:, 0, 1] - r[:, 1, 0]) / (4 * denom[:, 3]),
        denom[:, 3]
    ], 1)

    cases = torch.stack([case0, case1, case2, case3], 1)

    quaternions = cases[torch.arange(n, dtype=torch.long),
                        torch.argmax(denom.detach(), 1)]
    return quaternions.view(*batch_dims, 4)


def quaternions_to_eazyz(q):
    """Map batch of quaternion to Euler angles ZYZ. Output is not mod 2pi."""
    eps = 1E-6
    return torch.stack([
        torch.atan2(q[:, 1] * q[:, 2] - q[:, 0] * q[:, 3],
                    q[:, 0] * q[:, 2] + q[:, 1] * q[:, 3]),
        torch.acos(torch.clamp(q[:, 3] ** 2 - q[:, 0] ** 2
                               - q[:, 1] ** 2 + q[:, 2] ** 2,
                               -1.0+eps, 1.0-eps)),
        torch.atan2(q[:, 0] * q[:, 3] + q[:, 1] * q[:, 2],
                    q[:, 1] * q[:, 3] - q[:, 0] * q[:, 2])
    ], 1)


def so3_matrix_to_eazyz(r):
    """Map batch of SO(3) matrices to Euler angles ZYZ."""
    return quaternions_to_eazyz(so3_matrix_to_quaternions(r))


def quaternions_to_so3_matrix(q):
    """Normalises q and maps to group matrix."""
    q = q / q.norm(p=2, dim=-1, keepdim=True)
    r, i, j, k = q[..., 0], q[..., 1], q[..., 2], q[..., 3]

    return torch.stack([
        r*r - i*i - j*j + k*k, 2*(r*i + j*k), 2*(r*j - i*k),
        2*(r*i - j*k), -r*r + i*i - j*j + k*k, 2*(i*j + r*k),
        2*(r*j + i*k), 2*(i*j - r*k), -r*r - i*i + j*j + k*k
    ], -1).view(*q.shape[:-1], 3, 3)


def _z_rot_mat(angle, l):
    m = angle.new_zeros((angle.size(0), 2 * l + 1, 2 * l + 1))

    inds = torch.arange(
        0, 2 * l + 1, 1, dtype=torch.long, device=angle.device)
    reversed_inds = torch.arange(
        2 * l, -1, -1, dtype=torch.long, device=angle.device)

    frequencies = torch.arange(
        l, -l - 1, -1, dtype=angle.dtype, device=angle.device)[None]

    m[:, inds, reversed_inds] = torch.sin(frequencies * angle[:, None])
    m[:, inds, inds] = torch.cos(frequencies * angle[:, None])
    return m


class JContainer:
    data = {}

    @classmethod
    def get(cls, device):
        if str(device) in cls.data:
            return cls.data[str(device)]

        from lie_learn.representations.SO3.pinchon_hoggan.pinchon_hoggan_dense \
            import Jd as Jd_np

        device_data = [torch.tensor(J, dtype=torch.float32, device=device)
                       for J in Jd_np]
        cls.data[str(device)] = device_data

        return device_data


def wigner_d_matrix(angles, degree):
    """Create wigner D matrices for batch of ZYZ Euler anglers for degree l."""
    J = JContainer.get(angles.device)[degree][None]
    x_a = _z_rot_mat(angles[:, 0], degree)
    x_b = _z_rot_mat(angles[:, 1], degree)
    x_c = _z_rot_mat(angles[:, 2], degree)
    return x_a.matmul(J).matmul(x_b).matmul(J).matmul(x_c)


def block_wigner_matrix_multiply(angles, data, max_degree):
    """Transform data using wigner d matrices for all degrees.

    vector_dim is dictated by max_degree by the expression:
    vector_dim = \sum_{i=0}^max_degree (2 * max_degree + 1) = (max_degree+1)^2

    The representation is the direct sum of the irreps of the degrees up to max.
    The computation is equivalent to a block-wise matrix multiply.

    The data are the Fourier modes of a R^{data_dim} signal.

    Input:
    - angles (batch, 3)  ZYZ Euler angles
    - vector (batch, vector_dim, data_dim)

    Output: (batch, vector_dim, data_dim)
    """
    outputs = []
    start = 0
    for degree in range(max_degree+1):
        dim = 2 * degree + 1
        matrix = wigner_d_matrix(angles, degree)
        outputs.append(matrix.bmm(data[:, start:start+dim, :]))
        start += dim
    return torch.cat(outputs, 1)


def random_quaternions(n, dtype=torch.float32, device=None):
    u1, u2, u3 = torch.rand(3, n, dtype=dtype, device=device)
    return torch.stack((
        torch.sqrt(1-u1) * torch.sin(2 * math.pi * u2),
        torch.sqrt(1-u1) * torch.cos(2 * math.pi * u2),
        torch.sqrt(u1) * torch.sin(2 * math.pi * u3),
        torch.sqrt(u1) * torch.cos(2 * math.pi * u3),
    ), 1)
