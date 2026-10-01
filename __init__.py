# -*- coding: utf-8 -*-
"""
BSAI Hardware Performance Monitor —— ComfyUI 插件入口
======================================================
- 注册 `BSAIHardwarePerfMonitor` 节点（nodes.py）
- 注册 `GET /bsai/perf` 数据端点，供前端仪表盘轮询
- 声明 `web/` 目录，前端 HUD 自动加载

路由注册采用与 BSAI-Contextual-Series 相同的 PromptServer 模式，
并做容错：极端情况下 instance 不可用时插件仍可加载（仅缺路由）。
"""

import os
import json
import asyncio

import server
from aiohttp import web

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from .perf_collector import collect_all

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]


async def bsai_perf(request):
    """四硬件实时性能数据（前端仪表盘轮询）。

    关键：collect_all() 内含同步 HTTP 轮询（XPU worker / NPU 健康检查）。
    绝不能直接在事件循环主线程里同步执行，否则会阻塞整个 ComfyUI server
    （前端卡死在启动 logo、API 全部超时）。
    这里用 asyncio.to_thread 把同步采集放到工作线程，事件循环保持响应。
    """
    try:
        data = await asyncio.to_thread(collect_all)
        return web.json_response(data)
    except Exception as e:
        return web.json_response({"ts": 0, "error": str(e)})


try:
    _ps = server.PromptServer.instance
    if _ps is not None and hasattr(_ps, "routes"):
        _ps.routes.get("/bsai/perf")(bsai_perf)
        print("[BSAI-Perf] GET /bsai/perf 已注册（四硬件性能监视，异步采集）")
    else:
        print("[BSAI-Perf] PromptServer.instance 未就绪，/bsai/perf 路由稍后由节点触发注册")
except Exception as e:
    print("[BSAI-Perf] 路由注册容错跳过:", e)
