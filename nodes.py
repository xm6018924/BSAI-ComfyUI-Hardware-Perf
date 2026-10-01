# -*- coding: utf-8 -*-
"""
BSAI Hardware Performance Monitor —— 节点定义
==============================================
提供两个能力：
1. `BSAIHardwarePerfMonitor` 节点：放进工作流即可在节点上看到四硬件实时报告
   （前端仪表盘 HUD 会自动出现在画布右上角，无需节点）。
2. 前端 HUD 数据由 /bsai/perf 端点提供（见 __init__.py）。
"""

import os
import json
import time

from .perf_collector import collect_all


def _ensure_perf_route():
    """幂等补注册 /bsai/perf 路由（模块加载时 instance 未就绪则节点运行时补上）。"""
    try:
        import server
        ps = server.PromptServer.instance
        if ps is None or not hasattr(ps, "routes"):
            return
        for r in ps.routes.routes():
            if getattr(r, "path", "") == "/bsai/perf":
                return
        from aiohttp import web
        from .perf_collector import collect_all as _ca

        async def _handler(request):
            try:
                return web.json_response(_ca())
            except Exception as e:
                return web.json_response({"ts": 0, "error": str(e)})

        ps.routes.get("/bsai/perf")(_handler)
        print("[BSAI-Perf] GET /bsai/perf 已补注册")
    except Exception as e:
        print("[BSAI-Perf] 补注册失败:", e)


class BSAIHardwarePerfMonitor:
    """BSAI 硬件性能监视器 —— 采样瞬间各硬件实时参数（GPU/XPU/NPU/CPU）"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "refresh_interval": ("INT", {
                    "default": 1000, "min": 200, "max": 10000, "step": 100,
                    "tooltip": "HUD 仪表盘刷新间隔（毫秒）"}),
                "show_hud": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "是否显示画布右上角浮动仪表盘（拖动可移动，双击可折叠）"}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("报告 / report",)
    OUTPUT_NODE = True
    FUNCTION = "run"
    CATEGORY = "BSAI/性能监视"

    def run(self, refresh_interval=1000, show_hud=True):
        _ensure_perf_route()
        data = collect_all()
        lines = [
            "BSAI 四硬件性能（%s）" % time.strftime("%H:%M:%S"),
            "",
            "RTX 5090 (GPU1) | 采样主力",
        ]
        g1 = data["gpu1"]
        if g1["ok"]:
            lines.append(
                "  利用率 %d%% | 显存 %d/%dMB | %d°C | %.0fW/%.0fW"
                % (g1["util"], g1["mem_used_mb"], g1["mem_total_mb"],
                   g1["temp_c"], g1["power_w"], g1["power_max_w"]))
        else:
            lines.append("  不可用（pynvml 读取失败）")
        lines.append("")
        lines.append("Intel 核显 XPU (GPU0) | VAE decode (8190)")
        g0 = data["gpu0"]
        if g0["online"]:
            lines.append("  在线 | 显存 %d/%dMB | 响应 %.1fms | %s"
                         % (g0["vram_used_mb"], g0["vram_total_mb"],
                            g0["latency_ms"], g0["device_name"]))
        else:
            lines.append("  离线（未启动 BSAI_H3VM_双卡模式.bat）")
        lines.append("")
        lines.append("Intel AI Boost NPU | 人脸检测 (8191)")
        n = data["npu"]
        if n["online"]:
            lines.append("  在线 | %d 模型 | 响应 %.1fms" % (n["models"], n["latency_ms"]))
        else:
            lines.append("  离线")
        lines.append("")
        c = data["cpu"]
        if c["ok"]:
            lines.append("CPU | %d%% | 内存 %d%% (%d/%dMB)"
                         % (c["util"], c["ram_pct"], c["ram_used_mb"], c["ram_total_mb"]))
        m = data["main"]
        if m["ok"]:
            lines.append("主进程显存 | %d%% (%d/%dMB)"
                         % (m["pct"], m["used_mb"], m["total_mb"]))
        return ("\n".join(lines),)


NODE_CLASS_MAPPINGS = {
    "BSAIHardwarePerfMonitor": BSAIHardwarePerfMonitor,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BSAIHardwarePerfMonitor": "BSAI 硬件性能监视器 / Hardware Perf Monitor",
}
