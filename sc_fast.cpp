#include <cstdint>
#include <cstddef>
#include <cstring>
#include <cerrno>
#include <algorithm>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/videodev2.h>

extern "C" {

static int xioctl(int fd, unsigned long request, void* arg) {
    int r;
    do {
        r = ioctl(fd, request, arg);
    } while (r == -1 && errno == EINTR);
    return r;
}

void sc_swap_buffers(uint8_t* __restrict a, uint8_t* __restrict b, size_t n) {
    if (!a || !b || n == 0) return;
    size_t i = 0;
    uint64_t* a64 = reinterpret_cast<uint64_t*>(a);
    uint64_t* b64 = reinterpret_cast<uint64_t*>(b);
    size_t n64 = n / 8;
    for (size_t k = 0; k < n64; ++k) {
        uint64_t tmp = a64[k];
        a64[k] = b64[k];
        b64[k] = tmp;
    }
    for (i = n64 * 8; i < n; ++i) {
        uint8_t tmp = a[i];
        a[i] = b[i];
        b[i] = tmp;
    }
}

void sc_copy_swap_uv(
    const uint8_t* __restrict src_u, int src_u_stride,
    const uint8_t* __restrict src_v, int src_v_stride,
    uint8_t* __restrict dst_u, int dst_u_stride,
    uint8_t* __restrict dst_v, int dst_v_stride,
    int cw, int ch
) {
    if (!src_u || !src_v || !dst_u || !dst_v || cw <= 0 || ch <= 0 || cw > 16384 || ch > 16384) return;
    if (src_u_stride < cw || src_v_stride < cw || dst_u_stride < cw || dst_v_stride < cw) return;
    if (src_u_stride == cw && src_v_stride == cw && dst_u_stride == cw && dst_v_stride == cw) {
        size_t total = static_cast<size_t>(cw) * static_cast<size_t>(ch);
        std::memcpy(dst_u, src_v, total);
        std::memcpy(dst_v, src_u, total);
        return;
    }
    for (int r = 0; r < ch; ++r) {
        std::memcpy(dst_u + static_cast<size_t>(r) * dst_u_stride, src_v + static_cast<size_t>(r) * src_v_stride, static_cast<size_t>(cw));
        std::memcpy(dst_v + static_cast<size_t>(r) * dst_v_stride, src_u + static_cast<size_t>(r) * src_u_stride, static_cast<size_t>(cw));
    }
}

void sc_resize_rgb24(
    const uint8_t* __restrict src, int sw, int sh,
    uint8_t* __restrict dst, int dw, int dh
) {
    if (!src || !dst || sw <= 0 || sh <= 0 || dw <= 0 || dh <= 0 || sw > 16384 || sh > 16384 || dw > 16384 || dh > 16384) return;
    for (int y = 0; y < dh; ++y) {
        int sy = std::min(sh - 1, static_cast<int>((static_cast<int64_t>(y) * sh) / dh));
        const uint8_t* srow = src + static_cast<size_t>(sy) * sw * 3;
        uint8_t* drow = dst + static_cast<size_t>(y) * dw * 3;
        for (int x = 0; x < dw; ++x) {
            size_t sx = static_cast<size_t>(std::min(sw - 1, static_cast<int>((static_cast<int64_t>(x) * sw) / dw))) * 3;
            drow[x * 3 + 0] = srow[sx + 0];
            drow[x * 3 + 1] = srow[sx + 1];
            drow[x * 3 + 2] = srow[sx + 2];
        }
    }
}

size_t sc_i420_pack_contiguous(
    const uint8_t* __restrict y_plane, int y_stride,
    const uint8_t* __restrict u_plane, int u_stride,
    const uint8_t* __restrict v_plane, int v_stride,
    uint8_t* __restrict dst, int width, int height
) {
    if (!y_plane || !u_plane || !v_plane || !dst || width <= 0 || height <= 0 || width > 16384 || height > 16384) return 0;
    const int cw = width / 2;
    const int ch = height / 2;
    if (y_stride < width || u_stride < cw || v_stride < cw) return 0;
    uint8_t* out = dst;
    if (y_stride == width) {
        std::memcpy(out, y_plane, static_cast<size_t>(width) * height);
        out += static_cast<size_t>(width) * height;
    } else {
        for (int r = 0; r < height; ++r) {
            std::memcpy(out, y_plane + static_cast<size_t>(r) * y_stride, static_cast<size_t>(width));
            out += width;
        }
    }
    if (u_stride == cw) {
        std::memcpy(out, u_plane, static_cast<size_t>(cw) * ch);
        out += static_cast<size_t>(cw) * ch;
    } else {
        for (int r = 0; r < ch; ++r) {
            std::memcpy(out, u_plane + static_cast<size_t>(r) * u_stride, static_cast<size_t>(cw));
            out += cw;
        }
    }
    if (v_stride == cw) {
        std::memcpy(out, v_plane, static_cast<size_t>(cw) * ch);
        out += static_cast<size_t>(cw) * ch;
    } else {
        for (int r = 0; r < ch; ++r) {
            std::memcpy(out, v_plane + static_cast<size_t>(r) * v_stride, static_cast<size_t>(cw));
            out += cw;
        }
    }
    return static_cast<size_t>(out - dst);
}

size_t sc_i420_to_yuyv(
    const uint8_t* __restrict y_plane, int y_stride,
    const uint8_t* __restrict u_plane, int u_stride,
    const uint8_t* __restrict v_plane, int v_stride,
    uint8_t* __restrict dst_yuyv, int width, int height
) {
    if (!y_plane || !u_plane || !v_plane || !dst_yuyv || width <= 0 || height <= 0 || width > 16384 || height > 16384) return 0;
    const int pairs = width / 2;
    if (y_stride < width || u_stride < pairs || v_stride < pairs) return 0;
    for (int r = 0; r < height; ++r) {
        const uint8_t* yrow = y_plane + static_cast<size_t>(r) * y_stride;
        const uint8_t* urow = u_plane + static_cast<size_t>(r / 2) * u_stride;
        const uint8_t* vrow = v_plane + static_cast<size_t>(r / 2) * v_stride;
        uint8_t* drow = dst_yuyv + static_cast<size_t>(r) * width * 2;
        for (int c = 0; c < pairs; ++c) {
            drow[c * 4 + 0] = yrow[c * 2 + 0];
            drow[c * 4 + 1] = urow[c];
            drow[c * 4 + 2] = yrow[c * 2 + 1];
            drow[c * 4 + 3] = vrow[c];
        }
    }
    return static_cast<size_t>(width) * height * 2;
}

int sc_v4l2_query_device(
    int fd,
    char* driver_out32,
    char* card_out32,
    char* bus_out32,
    uint32_t* caps_out
) {
    if (fd < 0) return -1;
    struct v4l2_capability cap;
    std::memset(&cap, 0, sizeof(cap));
    if (xioctl(fd, VIDIOC_QUERYCAP, &cap) < 0) {
        return -1;
    }
    if (driver_out32) {
        std::strncpy(driver_out32, reinterpret_cast<const char*>(cap.driver), 31);
        driver_out32[31] = '\0';
    }
    if (card_out32) {
        std::strncpy(card_out32, reinterpret_cast<const char*>(cap.card), 31);
        card_out32[31] = '\0';
    }
    if (bus_out32) {
        std::strncpy(bus_out32, reinterpret_cast<const char*>(cap.bus_info), 31);
        bus_out32[31] = '\0';
    }
    uint32_t eff_caps = (cap.capabilities & V4L2_CAP_DEVICE_CAPS) ? cap.device_caps : cap.capabilities;
    if (caps_out) {
        *caps_out = eff_caps;
    }
    return 0;
}

int sc_v4l2_configure_output(
    int fd,
    int width,
    int height,
    uint32_t fourcc,
    int* stride_out,
    int* imagesize_out
) {
    if (fd < 0 || width <= 0 || height <= 0 || width > 16384 || height > 16384) return -1;
    struct v4l2_format fmt;
    std::memset(&fmt, 0, sizeof(fmt));
    fmt.type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
    fmt.fmt.pix.width = static_cast<uint32_t>(width);
    fmt.fmt.pix.height = static_cast<uint32_t>(height);
    fmt.fmt.pix.pixelformat = fourcc;
    fmt.fmt.pix.field = V4L2_FIELD_NONE;
    fmt.fmt.pix.colorspace = V4L2_COLORSPACE_SRGB;
    uint64_t pixels = static_cast<uint64_t>(width) * static_cast<uint64_t>(height);
    if (fourcc == V4L2_PIX_FMT_YUV420) {
        fmt.fmt.pix.bytesperline = static_cast<uint32_t>(width);
        fmt.fmt.pix.sizeimage = static_cast<uint32_t>((pixels * 3) / 2);
    } else if (fourcc == V4L2_PIX_FMT_YUYV) {
        fmt.fmt.pix.bytesperline = static_cast<uint32_t>(width * 2);
        fmt.fmt.pix.sizeimage = static_cast<uint32_t>(pixels * 2);
    } else {
        fmt.fmt.pix.bytesperline = static_cast<uint32_t>(width * 3);
        fmt.fmt.pix.sizeimage = static_cast<uint32_t>(pixels * 3);
    }
    if (xioctl(fd, VIDIOC_S_FMT, &fmt) < 0) {
        return -1;
    }
    if (stride_out) *stride_out = static_cast<int>(fmt.fmt.pix.bytesperline);
    if (imagesize_out) *imagesize_out = static_cast<int>(fmt.fmt.pix.sizeimage);
    return 0;
}

void sc_ws_xor_mask(uint8_t* __restrict data, size_t len, const uint8_t mask4[4]) {
    if (!data || !mask4 || len == 0) return;
    uint32_t m32 = 0;
    std::memcpy(&m32, mask4, 4);
    uint64_t m64 = (static_cast<uint64_t>(m32) << 32) | static_cast<uint64_t>(m32);
    size_t i = 0;
    size_t n64 = len / 8;
    uint64_t* d64 = reinterpret_cast<uint64_t*>(data);
    for (size_t k = 0; k < n64; ++k) {
        d64[k] ^= m64;
    }
    for (i = n64 * 8; i < len; ++i) {
        data[i] ^= mask4[i & 3];
    }
}

void sc_xdr_pack_u32_2(uint32_t a, uint32_t b, uint8_t out8[8]) {
    if (!out8) return;
    out8[0] = static_cast<uint8_t>((a >> 24) & 0xFF);
    out8[1] = static_cast<uint8_t>((a >> 16) & 0xFF);
    out8[2] = static_cast<uint8_t>((a >> 8) & 0xFF);
    out8[3] = static_cast<uint8_t>(a & 0xFF);
    out8[4] = static_cast<uint8_t>((b >> 24) & 0xFF);
    out8[5] = static_cast<uint8_t>((b >> 16) & 0xFF);
    out8[6] = static_cast<uint8_t>((b >> 8) & 0xFF);
    out8[7] = static_cast<uint8_t>(b & 0xFF);
}

uint32_t sc_crc32_ieee(const uint8_t* data, size_t len) {
    if (!data && len > 0) return 0;
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; ++i) {
        crc ^= data[i];
        for (int b = 0; b < 8; ++b) {
            uint32_t mask = -(crc & 1u);
            crc = (crc >> 1) ^ (0xEDB88320u & mask);
        }
    }
    return ~crc;
}

void sc_copy_strided_rows(
    const uint8_t* __restrict src, int src_stride,
    uint8_t* __restrict dst, int dst_stride,
    int row_bytes, int height
) {
    if (!src || !dst || row_bytes <= 0 || height <= 0 || src_stride < row_bytes || dst_stride < row_bytes) return;
    if (src_stride == row_bytes && dst_stride == row_bytes) {
        std::memcpy(dst, src, static_cast<size_t>(row_bytes) * static_cast<size_t>(height));
        return;
    }
    for (int r = 0; r < height; ++r) {
        std::memcpy(dst + static_cast<size_t>(r) * dst_stride,
                    src + static_cast<size_t>(r) * src_stride,
                    static_cast<size_t>(row_bytes));
    }
}

void sc_rgb24_to_bgrx32(
    const uint8_t* __restrict src_rgb, int src_stride,
    uint8_t* __restrict dst_bgrx, int dst_stride,
    int width, int height
) {
    if (!src_rgb || !dst_bgrx || width <= 0 || height <= 0 || width > 16384 || height > 16384) return;
    if (src_stride < width * 3 || dst_stride < width * 4) return;
    for (int r = 0; r < height; ++r) {
        const uint8_t* srow = src_rgb + static_cast<size_t>(r) * src_stride;
        uint32_t* drow = reinterpret_cast<uint32_t*>(dst_bgrx + static_cast<size_t>(r) * dst_stride);
        for (int c = 0; c < width; ++c) {
            uint32_t R = srow[c * 3 + 0];
            uint32_t G = srow[c * 3 + 1];
            uint32_t B = srow[c * 3 + 2];
            drow[c] = (0xFF000000u) | (R << 16) | (G << 8) | B;
        }
    }
}

void sc_bgrx32_to_rgb24_resize(
    const uint8_t* __restrict src_bgrx, int sw, int sh, int src_stride,
    uint8_t* __restrict dst_rgb, int dw, int dh
) {
    if (!src_bgrx || !dst_rgb || sw <= 0 || sh <= 0 || dw <= 0 || dh <= 0 || sw > 16384 || sh > 16384 || dw > 16384 || dh > 16384) return;
    if (src_stride <= 0) src_stride = sw * 4;
    if (src_stride < sw * 4) return;
    if (sw == dw && sh == dh) {
        for (int y = 0; y < dh; ++y) {
            const uint8_t* srow = src_bgrx + static_cast<size_t>(y) * src_stride;
            uint8_t* drow = dst_rgb + static_cast<size_t>(y) * dw * 3;
            for (int x = 0; x < dw; ++x) {
                drow[x * 3 + 0] = srow[x * 4 + 2];
                drow[x * 3 + 1] = srow[x * 4 + 1];
                drow[x * 3 + 2] = srow[x * 4 + 0];
            }
        }
        return;
    }
    for (int y = 0; y < dh; ++y) {
        int sy = std::min(sh - 1, static_cast<int>((static_cast<int64_t>(y) * sh) / dh));
        const uint8_t* srow = src_bgrx + static_cast<size_t>(sy) * src_stride;
        uint8_t* drow = dst_rgb + static_cast<size_t>(y) * dw * 3;
        for (int x = 0; x < dw; ++x) {
            size_t sx = static_cast<size_t>(std::min(sw - 1, static_cast<int>((static_cast<int64_t>(x) * sw) / dw))) * 4;
            drow[x * 3 + 0] = srow[sx + 2];
            drow[x * 3 + 1] = srow[sx + 1];
            drow[x * 3 + 2] = srow[sx + 0];
        }
    }
}

}
