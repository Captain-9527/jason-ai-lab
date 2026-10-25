"""模型坐标协议适配：所有下游裁剪只接受处理后图片像素，不猜测坐标制。"""
import math

from ..schemas import PipelineError
from .table import bbox


def reference_size(width, height, opts):
    """复现配置指定的服务端等比例、倍数对齐缩放；参数须与实际服务一致。"""
    resize = opts["bbox_resize"]
    factor, minimum, maximum = resize["factor"], resize["min_pixels"], resize["max_pixels"]
    w = max(factor, round(width / factor) * factor)
    h = max(factor, round(height / factor) * factor)
    if w * h > maximum:
        scale = math.sqrt(width * height / maximum)
        w = max(factor, math.floor(width / scale / factor) * factor)
        h = max(factor, math.floor(height / scale / factor) * factor)
    elif w * h < minimum:
        scale = math.sqrt(minimum / (width * height))
        w = math.ceil(width * scale / factor) * factor
        h = math.ceil(height * scale / factor) * factor
    return w, h


def coordinate_contract(cfg):
    opts = (cfg or {}).get("model", {})
    contract = {"format": opts.get("bbox_format", "pixel"),
            "resize": opts.get("bbox_resize"),
            "reference": "所指页面或裁剪图自身；局部视图以当前输入图片为准，不指向附加放大图",
            "internal_candidate_coordinates": "候选中已有证据均为处理后完整页面像素，不能直接当作归一化输出"}
    if contract['format'] in {'norm999', 'norm1000'}:
        limit = 999 if contract['format'] == 'norm999' else 1000
        contract.update(range=[0, limit], example_full_image=[0, 0, limit, limit],
                        conversion=f'先读像素位置，再按x/输入宽度*{limit}、y/输入高度*{limit}换算；不能把图片宽高当作bbox上限')
    return contract


def to_pixels(value, width, height, cfg=None, warnings=None):
    opts = (cfg or {}).get("model", {})
    mode = opts.get("bbox_format", "pixel")
    if not isinstance(value, list) or len(value) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
        raise PipelineError("模型 bbox 必须为四个有限数值")
    if value[0] >= value[2] or value[1] >= value[3]:
        raise PipelineError("模型 bbox 反向或为空")
    if mode in {"norm999", "norm1000"}:
        rw = rh = 999 if mode == "norm999" else 1000
    elif mode == "resized_pixel":
        rw, rh = reference_size(width, height, opts)
    elif mode == "pixel":
        rw, rh = width, height
    else:
        raise PipelineError("未知坐标协议")
    converted = [value[0] * width / rw, value[1] * height / rh,
                 value[2] * width / rw, value[3] * height / rh]
    tolerance = opts.get("bbox_clip_tolerance", .01)
    if (converted[0] < -width * tolerance or converted[1] < -height * tolerance or
            converted[2] > width * (1 + tolerance) or converted[3] > height * (1 + tolerance)):
        raise PipelineError("模型 bbox 严重越界，拒绝盲目截断")
    clipped = [max(0, converted[0]), max(0, converted[1]), min(width, converted[2]), min(height, converted[3])]
    if clipped != converted and warnings is not None:
        warnings.append("bbox 轻微越界，已裁到图片边界")
    return bbox(clipped, width, height)


def evidence_pixels(item, width, height, cfg=None, warnings=None):
    """显式声明可覆盖证据坐标制；必须匹配上传视图尺寸，绝不按数值猜测。"""
    mode = item.get('bbox_format')
    size = item.get('image_size')
    if 'bbox_format' not in item:
        if size is not None:
            raise PipelineError('image_size必须与bbox_format同时声明')
        return to_pixels(item.get('bbox'), width, height, cfg, warnings)
    if not isinstance(mode, str) or mode not in {'pixel', 'norm999', 'norm1000'}:
        raise PipelineError('证据bbox_format仅支持pixel/norm999/norm1000，不接受未知服务端缩放')
    if (not isinstance(size, list) or len(size) != 2
            or any(type(v) is not int for v in size) or size != [width, height]):
        raise PipelineError('证据image_size必须等于当前上传视图的[width,height]，不能使用整页或服务端猜测尺寸')
    opts = {**(cfg or {}).get('model', {}), 'bbox_format': mode}
    return to_pixels(item.get('bbox'), width, height, {'model': opts}, warnings)


def emit_warnings(model, stage, warnings):
    trace = getattr(model, "trace", None)
    if trace:
        for warning in warnings:
            trace.emit("coordinate_warning", stage=stage, warning=warning)
