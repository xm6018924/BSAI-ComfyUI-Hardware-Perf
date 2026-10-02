// ============================================================================
// BSAI Hardware Performance Dashboard —— 画布浮动四硬件仪表盘
// ----------------------------------------------------------------------------
// 右上角浮动 HUD，五个圆盘指针仪表（RTX 5090 / Intel 核显 XPU / NPU / CPU / RAM），
// 0.5s 轮询 GET /bsai/perf，指针平滑跳动显示实时参数。
// 交互：按住拖动移动；双击折叠/展开；可随时关闭（点 ×）。
// ============================================================================

(function () {
    const PERF_URL = "/bsai/perf";
    const POLL_MS = 500;

    // ---------------- 仪表盘配色（荧光 + 深色玻璃拟态） ----------------
    const THEME = {
        panelBg: "rgba(16, 18, 28, 0.88)",
        panelBorder: "rgba(120, 160, 255, 0.35)",
        glow: "rgba(90, 140, 255, 0.45)",
        track: "rgba(255,255,255,0.10)",
        scale: "rgba(255,255,255,0.35)",
        text: "#e8ecff",
        sub: "#8a94b8",
        arc: (c) => `rgba(${c},0.9)`,
        colors: {
            gpu1: "0,200,255",   // 5090 青
            gpu0: "160,120,255", // XPU 紫
            npu: "255,170,60",   // NPU 橙
            cpu: "80,230,140",   // CPU 绿
            ram: "255,100,120",  // RAM 红
        },
    };

    let panel = null;
    let collapsed = false;
    let dragState = null;
    let timer = null;
    const VEIL_SPAN = 65;   // 指针拖尾光幕扫过的角度（从当前角度向后 65°，渐变到透明）

    // ---------------- SVG 圆盘仪表 ----------------
    function buildGauge(id, label, color, unit) {
        const size = 92;
        const cx = size / 2, cy = size / 2;
        const rOuter = 50, rTick = 44, rArc = 42;
        const angle0 = -135; // 起始角（度，0=右）
        const sweep = 270;

        const polar = (angleDeg, radius) => {
            const a = (angleDeg - 90) * Math.PI / 180;
            return [cx + radius * Math.cos(a), cy + radius * Math.sin(a)];
        };

        // 刻度线（0..100 每 10）
        let ticks = "";
        for (let v = 0; v <= 100; v += 10) {
            const [x1, y1] = polar(angle0 + sweep * v / 100, rTick);
            const [x2, y2] = polar(angle0 + sweep * v / 100, rTick - 4);
            ticks += `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${THEME.scale}" stroke-width="1"/>`;
        }

        // 刻度弧
        const arcPath = (startV, endV, r, stroke, width) => {
            const [x1, y1] = polar(angle0 + sweep * startV / 100, r);
            const [x2, y2] = polar(angle0 + sweep * endV / 100, r);
            const large = (endV - startV) > 50 ? 1 : 0;
            return `<path d="M ${x1.toFixed(1)} ${y1.toFixed(1)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(1)} ${y2.toFixed(1)}" fill="none" stroke="${stroke}" stroke-width="${width}" stroke-linecap="round"/>`;
        };

        // 指针拖尾：渐变到透明的弧形光幕（双层模糊光带），从指针当前位置向后 VEIL_SPAN 度渐隐。
        // 光幕沿指针尖端轨迹画弧（无圆心伸出的线段），随指针平滑旋转——不会出现"第二根指针/实心色块"。
        const tipR = rTick - 6;                      // 主指针尖端半径 38
        const tipP = polar(angle0, tipR);
        const rV = tipR * 1.05;                      // 光幕弧半径（略超尖端，光带悬浮在指针轨迹上）
        const veilA0 = angle0, veilA1 = angle0 + VEIL_SPAN;
        const veilPath = (() => {
            const [x1, y1] = polar(veilA0, rV);
            const [x2, y2] = polar(veilA1, rV);
            const large = Math.abs(veilA1 - veilA0) > 180 ? 1 : 0;
            return `M ${x1.toFixed(1)} ${y1.toFixed(1)} A ${rV} ${rV} 0 ${large} 1 ${x2.toFixed(1)} ${y2.toFixed(1)}`;
        })();
        const veilG0 = polar(veilA0, rV);            // 渐变透明端（旧角度侧）
        const veilG1 = polar(veilA1, rV);            // 渐变亮端（当前指针角度侧）

        const svg = `
        <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" class="bsai-gauge-svg">
          <defs>
            <linearGradient id="bsai-ptr-g-${id}" x1="${cx}" y1="${cy}" x2="${tipP[0].toFixed(1)}" y2="${tipP[1].toFixed(1)}" gradientUnits="userSpaceOnUse">
              <stop offset="0" stop-color="${THEME.arc(color)}" stop-opacity="0.2"/>
              <stop offset="0.55" stop-color="${THEME.arc(color)}" stop-opacity="0.65"/>
              <stop offset="1" stop-color="${THEME.arc(color)}" stop-opacity="1"/>
            </linearGradient>
            <linearGradient id="bsai-veil-g-${id}" x1="${veilG0[0].toFixed(1)}" y1="${veilG0[1].toFixed(1)}" x2="${veilG1[0].toFixed(1)}" y2="${veilG1[1].toFixed(1)}" gradientUnits="userSpaceOnUse">
              <stop offset="0" stop-color="${THEME.arc(color)}" stop-opacity="0"/>
              <stop offset="0.65" stop-color="${THEME.arc(color)}" stop-opacity="0.25"/>
              <stop offset="1" stop-color="${THEME.arc(color)}" stop-opacity="0.5"/>
            </linearGradient>
          </defs>
          <circle cx="${cx}" cy="${cy}" r="${rOuter}" fill="rgba(255,255,255,0.03)" stroke="${THEME.track}" stroke-width="1"/>
          ${ticks}
          ${arcPath(0, 100, rArc, THEME.track, 3)}
          ${arcPath(0, 1, rArc, THEME.arc(color), 3)}
          <path id="bsai-veil-outer-${id}" class="bsai-gauge-veil" d="${veilPath}" fill="none"
                stroke="url(#bsai-veil-g-${id})" stroke-width="13" stroke-linecap="round"
                style="opacity:0; filter: blur(5px);"/>
          <path id="bsai-veil-core-${id}" class="bsai-gauge-veil" d="${veilPath}" fill="none"
                stroke="url(#bsai-veil-g-${id})" stroke-width="6" stroke-linecap="round"
                style="opacity:0; filter: blur(2.5px);"/>
          <line id="bsai-ptr-${id}" class="bsai-gauge-ptr" x1="${cx}" y1="${cy}" x2="${tipP[0].toFixed(1)}" y2="${tipP[1].toFixed(1)}"
                stroke="url(#bsai-ptr-g-${id})" stroke-width="2.5" stroke-linecap="round"
                style="filter: drop-shadow(0 0 4px ${THEME.arc(color)});"/>
          <circle cx="${cx}" cy="${cy}" r="4" fill="${THEME.arc(color)}"/>
          <text id="bsai-val-${id}" x="${cx}" y="${cy + 4}" text-anchor="middle" font-size="13" font-weight="700" fill="${THEME.text}" style="font-family:Consolas,monospace">0</text>
        </svg>`;

        const wrap = document.createElement("div");
        wrap.className = "bsai-gauge";
        wrap.innerHTML = `
          <div class="bsai-gauge-svg-wrap">${svg}</div>
          <div class="bsai-gauge-label">${label}</div>
          <div class="bsai-gauge-sub" id="bsai-sub-${id}">--</div>
        `;
        wrap.dataset.gaugeId = id;
        wrap.dataset.color = color;
        return wrap;
    }

    // 指针角度：0~100 -> 相对初始 -135° 旋转 0°~270°
    // 线初始画在 -135°（0% 刻度），CSS rotate 只做相对旋转，围绕 view-box 中心(59,59)
    function setGaugeValue(id, pct, valueText, subText, ok) {
        const ptr = document.getElementById(`bsai-ptr-${id}`);
        const val = document.getElementById(`bsai-val-${id}`);
        const sub = document.getElementById(`bsai-sub-${id}`);
        const wrap = document.querySelector(`.bsai-gauge[data-gauge-id="${id}"]`);
        if (!ptr || !val || !sub || !wrap) return;
        pct = Math.max(0, Math.min(100, pct));
        const angle = 270 * pct / 100;
        ptr.style.transform = `rotate(${angle.toFixed(1)}deg)`;
        // 指针拖尾光幕：从指针当前位置向后 VEIL_SPAN 度的一段弧形渐变光带，随指针平滑旋转；
        // 角度较小时自动变淡（光幕不溢出到起始刻度之外）。
        const veilCore = document.getElementById(`bsai-veil-core-${id}`);
        const veilOuter = document.getElementById(`bsai-veil-outer-${id}`);
        if (veilCore && veilOuter) {
            const rot = angle - VEIL_SPAN;
            const vis = Math.min(1, angle / VEIL_SPAN);
            const t = `rotate(${rot.toFixed(1)}deg)`;
            veilCore.style.transform = t;
            veilOuter.style.transform = t;
            veilCore.style.opacity = String(vis);
            veilOuter.style.opacity = String(vis * 0.9);
        }
        val.textContent = valueText;
        sub.textContent = subText || "--";
        wrap.style.opacity = ok === false ? "0.45" : "1";
        // 状态灯
        const led = wrap.querySelector(".bsai-gauge-led");
        if (led) led.style.background = ok === false ? "#555" : THEME.arc(wrap.dataset.color);
    }

    // ---------------- 面板 ----------------
    function buildPanel() {
        panel = document.createElement("div");
        panel.id = "bsai-perf-panel";
        panel.innerHTML = `
          <div class="bsai-perf-header">
            <span class="bsai-perf-title">BSAI 四硬件监视</span>
            <span class="bsai-perf-led" id="bsai-perf-led"></span>
            <button class="bsai-perf-btn" id="bsai-perf-collapse" title="折叠/展开">—</button>
            <button class="bsai-perf-btn" id="bsai-perf-close" title="关闭">×</button>
          </div>
          <div class="bsai-perf-body" id="bsai-perf-body"></div>
        `;
        const body = panel.querySelector("#bsai-perf-body");
        body.appendChild(buildGauge("gpu1", "RTX 5090 · GPU1", THEME.colors.gpu1, "%"));
        body.appendChild(buildGauge("gpu0", "核显 XPU · GPU0", THEME.colors.gpu0, "%"));
        body.appendChild(buildGauge("npu", "NPU · 检测", THEME.colors.npu, "%"));
        body.appendChild(buildGauge("cpu", "CPU", THEME.colors.cpu, "%"));
        body.appendChild(buildGauge("ram", "内存 RAM", THEME.colors.ram, "%"));

        // 底部状态条
        const footer = document.createElement("div");
        footer.className = "bsai-perf-footer";
        footer.id = "bsai-perf-footer";
        body.appendChild(footer);

        // 拖动：左键自由移动，释放左键就地悬停（无吸附、无停靠虚线框）
        // 性能要点：
        //   1) 拖动期间用 transform: translate 移动（合成层，不触发 layout/paint）
        //   2) 拖动中临时禁用 backdrop-filter（blur 每帧重算是卡顿主因）
        //   3) 边界保护：任何时刻至少 48px 留在视口内，永远拖得回
        const header = panel.querySelector(".bsai-perf-header");
        const MIN_VISIBLE = 48;
        let rafId = 0;
        let pendingDrag = null;

        function applyDrag() {
            rafId = 0;
            if (!dragState || !pendingDrag) return;
            panel.style.transform =
                `translate(${pendingDrag.left - dragState.baseLeft}px, ${pendingDrag.top - dragState.baseTop}px)`;
        }

        function clampDrag(e) {
            const vw = window.innerWidth, vh = window.innerHeight;
            let left = e.clientX - dragState.dx;
            let top = e.clientY - dragState.dy;
            left = Math.min(Math.max(left, MIN_VISIBLE - dragState.w), vw - MIN_VISIBLE);
            top = Math.min(Math.max(top, 0), vh - MIN_VISIBLE);
            return { left, top };
        }

        // 拖动改为 window 级事件（down 在 header，move/up/cancel 在 window）：
        //   1) 不依赖 pointer capture 传递事件——capture 丢失/失败（拖出窗口、合成事件、浏览器差异）时
        //      拖动依然全程跟随鼠标，且任何位置松手都会触发 endDrag 清理。
        //   2) 根治"面板自己乱跳、鼠标没法正常移动"：旧实现 pointerup 绑在 header 上，
        //      一旦 pointerup 丢失（快速操作/拖出窗口）dragState 卡死 + capture 未释放，
        //      面板持续被鼠标"劫持"跟随乱跳。
        function onDragMove(e) {
            if (!dragState) return;
            pendingDrag = clampDrag(e);
            if (!rafId) rafId = requestAnimationFrame(applyDrag);  // 每帧最多写一次 DOM
        }

        function endDrag(e) {
            if (!dragState) return;
            const wasDocked = dragState.wasDocked;
            if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
            if (pendingDrag) {
                panel.style.left = pendingDrag.left + "px";
                panel.style.top = pendingDrag.top + "px";
            }
            panel.style.transform = "";      // 收起合成层位移，转回 left/top 定位（就地悬停）
            panel.classList.remove("bsai-dragging");
            // 宽度恢复放在 autoDock 之后（autoDock 可能切换布局）
            pendingDrag = null;
            dragState = null;
            // 透明度 0 时：拖动中不隐藏（避免"拖一下面板就消失"）；松手后按鼠标实际位置定夺——
            // 鼠标还在面板上则背景板显现（悬停态），已在面板外则恢复背景板全透明（画布可见）。
            if (opacityVal === 0) {
                const r = panel.getBoundingClientRect();
                const inside = e.clientX >= r.left && e.clientX <= r.right &&
                               e.clientY >= r.top && e.clientY <= r.bottom;
                applyPanelAlpha(inside ? 1 : 0);
            } else {
                applyPanelAlpha(opacityVal / 100);
            }
            window.removeEventListener("pointermove", onDragMove);
            window.removeEventListener("pointerup", endDrag);
            window.removeEventListener("pointercancel", endDrag);
            autoDock(wasDocked);
            // autoDock 后按最终布局恢复宽度
            if (layoutMode === "vertical") {
                panel.style.width = "150px";
            } else {
                panel.style.width = "auto";
            }
        }

        header.addEventListener("pointerdown", (e) => {
            if (e.button !== 0 || e.target.tagName === "BUTTON") return;  // 仅左键拖动；右键留给透明度菜单
            // 记录拖拽前是否真正贴边（inline style 为 left:48px 或 right:0）
            const preRect = panel.getBoundingClientRect();
            const trulyDocked = (panel.style.left === "48px" || panel.style.right === "0");
            const wasDocked = trulyDocked;
            // 拖拽前：先把当前视觉位置换算成 left/top，清除 bottom/right 贴边约束
            const rect = preRect;
            panel.style.right = "auto";
            panel.style.bottom = "auto";
            panel.style.left = rect.left + "px";
            panel.style.top = rect.top + "px";
            dragState = {
                dx: e.clientX - panel.offsetLeft,
                dy: e.clientY - panel.offsetTop,
                baseLeft: panel.offsetLeft,   // 拖拽基准（transform 相对位移用）
                baseTop: panel.offsetTop,
                w: panel.offsetWidth,         // 尺寸缓存，避免反复 reflow
                h: panel.offsetHeight,
                wasDocked: wasDocked,
            };
            panel.style.width = dragState.w + "px";  // 锁定宽度，防止右缘拖动被压缩
            panel.classList.add("bsai-dragging");    // 禁用 backdrop-filter / transition
            panel.style.backdropFilter = "none";     // 拖动中强制关 blur（inline 优先于 CSS，防每帧重算卡顿）
            try { panel.setPointerCapture(e.pointerId); } catch (err) { /* capture 失败不阻塞：window 监听兜底 */ }
            window.addEventListener("pointermove", onDragMove);
            window.addEventListener("pointerup", endDrag);
            window.addEventListener("pointercancel", endDrag);
        });

        // ---------------- 右键透明度菜单（0~100：只作用于背景板，仪表盘始终全亮） ----------------
        let opacityVal = 100;
        let layoutMode = "vertical";  // vertical=贴边竖版, horizontal=悬浮横版
        let opMenu = null;

        // 布局切换：vertical（竖列）/ horizontal（横排）；dock=true 时吸附到边缘
        function applyLayout(mode, side, dock) {
            layoutMode = mode;
            if (mode === "horizontal") {
                panel.classList.add("bsai-layout-h");
                panel.classList.remove("bsai-layout-v");
                panel.style.borderRadius = "12px";
                panel.style.borderRight = "";
                panel.style.borderLeft = "";
                panel.style.borderBottom = "";
                panel.style.bottom = "auto";
                panel.style.top = "80px";
                panel.style.width = "auto";
                panel.style.maxWidth = "680px";
                panel.style.transform = "";
                panel.style.left = "0px";
                // 横版居中（下一帧测量实际宽度后修正 left）
                requestAnimationFrame(() => {
                    const w = panel.offsetWidth;
                    panel.style.left = Math.max(8, (window.innerWidth - w) / 2) + "px";
                });
                panel.style.boxShadow = "0 4px 24px rgba(0,0,0,0.5), 0 0 18px " + THEME.glow;
            } else {
                panel.classList.add("bsai-layout-v");
                panel.classList.remove("bsai-layout-h");
                panel.style.maxWidth = "none";
                panel.style.width = "150px";
                panel.style.transform = "";
                panel.style.borderRadius = "12px";
                panel.style.borderRight = "";
                panel.style.borderLeft = "";
                panel.style.borderBottom = "";
                panel.style.boxShadow = "0 4px 24px rgba(0,0,0,0.5), 0 0 18px " + THEME.glow;
                // 只有 dock=true 时才吸附贴边
                if (dock) {
                    panel.style.left = "auto";
                    panel.style.right = "auto";
                    panel.style.bottom = "auto";
                    panel.style.top = "auto";
                    if (side === "left") {
                        panel.style.left = "48px";
                        panel.style.borderRadius = "0 12px 12px 0";
                        panel.style.borderLeft = "none";
                        panel.style.boxShadow = "4px 4px 24px rgba(0,0,0,0.5), 0 0 18px " + THEME.glow;
                    } else {
                        panel.style.right = "0";
                        panel.style.bottom = "44px";
                        panel.style.borderRadius = "12px 0 0 0";
                        panel.style.borderRight = "none";
                        panel.style.borderBottom = "none";
                        panel.style.boxShadow = "-4px -4px 24px rgba(0,0,0,0.5), 0 0 18px " + THEME.glow;
                    }
                } else {
                    // 菜单切换竖版：保持当前位置，不吸附
                    if (!panel.style.left && !panel.style.right) {
                        panel.style.right = "8px";
                    }
                }
            }
        }

        // 拖动结束后：距边 <80px 吸附；从贴边拖到中间则自动横版
        function autoDock(wasDocked) {
            const r = panel.getBoundingClientRect();
            const vw = window.innerWidth;
            if (r.left < 60) {
                applyLayout("vertical", "left", true);
                // 保持松手时的垂直位置
                panel.style.bottom = "auto";
                panel.style.top = r.top + "px";
            } else if (r.right > vw - 80) {
                applyLayout("vertical", "right", true);
                panel.style.bottom = "auto";
                panel.style.top = r.top + "px";
            } else if (wasDocked) {
                // 从贴边拖到中间 → 自动横版
                applyLayout("horizontal", null, false);
            }
            // 已经是中间悬浮：保持当前布局
        }

        // 透明度只对"背景板"有效：背景色/边框/阴影/blur 随透明度缩放；
        // 仪表盘内容（指针/刻度/数值/标签）始终保持全亮。透明度 0 = 背景板完全透明（画布全可见）。
        function applyPanelAlpha(a) {
            a = Math.max(0, Math.min(1, a));
            panel.style.background = `rgba(16,18,28,${(0.88 * a).toFixed(3)})`;
            panel.style.borderColor = `rgba(120,160,255,${(0.35 * a).toFixed(3)})`;
            panel.style.boxShadow = `0 4px 24px rgba(0,0,0,${(0.5 * a).toFixed(3)}), 0 0 18px rgba(90,140,255,${(0.45 * a).toFixed(3)})`;
            panel.style.backdropFilter = a < 0.02 ? "none" : `blur(${(8 * a).toFixed(1)}px)`;
        }

        function applyOpacity(v) {
            opacityVal = Math.max(0, Math.min(100, Math.round(v)));
            applyPanelAlpha(opacityVal / 100);
            panel.style.opacity = "1";   // 内容恒全亮（透明度只对背景板有效）
            const oml = document.getElementById("bsai-op-val");
            const omr = document.getElementById("bsai-op-range");
            if (oml) oml.textContent = opacityVal + "%";
            if (omr) omr.value = opacityVal;
        }

        function closeOpMenu() {
            if (opMenu) { opMenu.remove(); opMenu = null; }
            document.removeEventListener("pointerdown", onDocPointerDown);
        }

        // 点击菜单外部关闭；菜单内 pointerdown 已 stopPropagation，不会走到这里
        function onDocPointerDown(e) {
            if (opMenu && !opMenu.contains(e.target)) closeOpMenu();
        }

        function showOpMenu(x, y) {
            closeOpMenu();
            opMenu = document.createElement("div");
            opMenu.id = "bsai-perf-opmenu";
            opMenu.innerHTML = `
              <div class="bsai-op-title">BSAI 仪表盘</div>
              <div class="bsai-op-row" style="margin-bottom:8px">
                <span style="font-size:11px;color:${THEME.sub}">布局</span>
                <span class="bsai-op-presets">
                  <button data-layout="vertical" class="${layoutMode==='vertical'?'active':''}">竖版</button>
                  <button data-layout="horizontal" class="${layoutMode==='horizontal'?'active':''}">横版</button>
                </span>
              </div>
              <div class="bsai-op-title" style="margin-bottom:4px">面板透明度</div>
              <input id="bsai-op-range" type="range" min="0" max="100" step="5" value="${opacityVal}">
              <div class="bsai-op-row">
                <span id="bsai-op-val">${opacityVal}%</span>
                <span class="bsai-op-presets">
                  <button data-v="0">0</button><button data-v="25">25</button>
                  <button data-v="50">50</button><button data-v="75">75</button><button data-v="100">100</button>
                </span>
              </div>`;
            opMenu.style.left = Math.max(4, Math.min(x, window.innerWidth - 190)) + "px";
            opMenu.style.top = Math.max(4, Math.min(y, window.innerHeight - 130)) + "px";
            document.body.appendChild(opMenu);
            opMenu.querySelector("#bsai-op-range").addEventListener("input", (e) => applyOpacity(parseInt(e.target.value, 10)));
            opMenu.querySelectorAll(".bsai-op-presets button[data-v]").forEach(b =>
                b.addEventListener("click", (e) => { e.stopPropagation(); applyOpacity(parseInt(b.dataset.v, 10)); }));
            opMenu.querySelectorAll(".bsai-op-presets button[data-layout]").forEach(b =>
                b.addEventListener("click", (e) => {
                    e.stopPropagation();
                    const m = b.dataset.layout;
                    if (m === "horizontal") {
                        applyLayout("horizontal", null, false);
                    } else {
                        applyLayout("vertical", "right", false);
                    }
                    closeOpMenu();
                }));
            opMenu.addEventListener("pointerdown", (e) => e.stopPropagation());
            document.addEventListener("pointerdown", onDocPointerDown);
        }

        panel.addEventListener("contextmenu", (e) => {
            e.preventDefault();
            showOpMenu(e.clientX, e.clientY);
        });
        // 透明度 0 时背景板透明：悬停原位置背景板临时完全显现（方便找回与右键）。
        // 拖动中不触发隐藏：拖动时鼠标会移出面板元素，若 mouseleave 生效面板会在拖动中消失。
        // mouseenter/mouseleave 不冒泡，鼠标在仪表盘之间移动不会触发，只有真正移出面板边界才隐藏。
        panel.addEventListener("mouseenter", () => { if (opacityVal === 0 && !dragState) applyPanelAlpha(1); });
        panel.addEventListener("mouseleave", () => { if (opacityVal === 0 && !dragState) applyPanelAlpha(0); });

        panel.querySelector("#bsai-perf-collapse").addEventListener("click", () => {
            collapsed = !collapsed;
            panel.querySelector("#bsai-perf-body").style.display = collapsed ? "none" : "flex";
            panel.querySelector("#bsai-perf-collapse").textContent = collapsed ? "+" : "—";
        });
        panel.querySelector("#bsai-perf-close").addEventListener("click", () => {
            stop();
            closeOpMenu();
            if (panel) panel.remove();
            panel = null;
        });

        document.body.appendChild(panel);
        // 初始定位：右下角贴边，留出缩放按钮空间（底部约 44px）
        panel.style.bottom = "44px";
        panel.style.right = "0";
    }

    // ---------------- 数据渲染 ----------------
    function render(data) {
        const led = document.getElementById("bsai-perf-led");
        const footer = document.getElementById("bsai-perf-footer");
        if (!data || data.error) {
            if (led) led.style.background = "#ff6060";
            if (footer) footer.textContent = "数据获取失败";
            return;
        }
        const g1 = data.gpu1 || {};
        const g0 = data.gpu0 || {};
        const np = data.npu || {};
        const cp = data.cpu || {};
        const mn = data.main || {};

        setGaugeValue("gpu1", g1.util || 0,
            `${g1.util ?? 0}%`,
            g1.ok ? `${(g1.mem_used_mb/1024).toFixed(1)}/${(g1.mem_total_mb/1024).toFixed(1)}GB · ${g1.temp_c}°C · ${g1.power_w}W` : "pynvml 不可用",
            g1.ok);

        setGaugeValue("gpu0", g0.util || 0,
            g0.online ? `${g0.util}%` : "N/A",
            g0.online ? `VAE decode · 引擎负载 · ${g0.vram_used_mb}MB` : "XPU worker 未启动",
            g0.online);

        setGaugeValue("npu", np.util || 0,
            np.online ? `${np.util}%` : "N/A",
            np.online ? `${np.models} 模型 · ${np.latency_ms}ms` : "NPU 服务离线",
            np.online);

        setGaugeValue("cpu", cp.util || 0,
            `${cp.util ?? 0}%`,
            cp.ok ? `内存 ${cp.ram_pct}% · ${(cp.ram_used_mb/1024).toFixed(1)}GB` : "不可用",
            cp.ok);

        setGaugeValue("ram", cp.ram_pct || 0,
            `${cp.ram_pct ?? 0}%`,
            cp.ok ? `${(cp.ram_used_mb/1024).toFixed(1)}/${(cp.ram_total_mb/1024).toFixed(1)}GB` : "不可用",
            cp.ok);

        if (led) led.style.background = "rgba(80,230,140,0.9)";
        if (footer) {
            const parts = [];
            if (mn.ok) parts.push(`主进程显存 ${mn.pct}%`);
            if (g1.ok) parts.push(`5090 ${g1.util}%`);
            if (np.online) parts.push(`NPU 在线`);
            if (g0.online) parts.push(`XPU 在线`);
            footer.textContent = parts.join("  ·  ") || "等待数据";
        }
    }

    async function poll() {
        try {
            const r = await fetch(PERF_URL, { cache: "no-store" });
            if (!r.ok) throw new Error(r.status);
            const data = await r.json();
            render(data);
        } catch (e) {
            render({ error: String(e) });
        }
    }

    function start() {
        if (timer) return;
        poll();
        timer = setInterval(poll, POLL_MS);
    }

    function stop() {
        if (timer) { clearInterval(timer); timer = null; }
    }

    // ---------------- 样式 ----------------
    const style = document.createElement("style");
    style.textContent = `
      #bsai-perf-panel {
        position: fixed;
        bottom: 0;
        right: 0;
        z-index: 99999;
        background: ${THEME.panelBg};
        border: 1px solid ${THEME.panelBorder};
        border-right: none;
        border-bottom: none;
        border-radius: 12px 0 0 0;
        box-shadow: -4px -4px 24px rgba(0,0,0,0.5), 0 0 18px ${THEME.glow};
        backdrop-filter: blur(8px);
        font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
        color: ${THEME.text};
        user-select: none;
        width: 150px;
      }
      #bsai-perf-panel .bsai-perf-header {
        display: flex; align-items: center; gap: 6px;
        padding: 6px 8px;
        cursor: move;
        border-bottom: 1px solid rgba(255,255,255,0.08);
      }
      #bsai-perf-panel .bsai-perf-title {
        font-size: 11px; font-weight: 700; letter-spacing: 0.3px;
        background: linear-gradient(90deg, #4db8ff, #a06fff);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        flex: 1; white-space: nowrap;
      }
      #bsai-perf-panel .bsai-perf-led {
        width: 8px; height: 8px; border-radius: 50%;
        background: #555;
        box-shadow: 0 0 6px currentColor;
      }
      #bsai-perf-panel .bsai-perf-btn {
        background: rgba(255,255,255,0.08); border: none; color: ${THEME.text};
        width: 20px; height: 20px; border-radius: 5px; cursor: pointer;
        font-size: 13px; line-height: 1;
      }
      #bsai-perf-panel .bsai-perf-btn:hover { background: rgba(255,255,255,0.2); }
      #bsai-perf-panel .bsai-perf-body {
        display: flex; flex-direction: column; gap: 2px; padding: 6px 4px;
        align-items: center;
      }
      /* 横版模式：横排一行 */
      #bsai-perf-panel.bsai-layout-h .bsai-perf-body {
        flex-direction: row; flex-wrap: wrap; gap: 6px; padding: 10px;
        justify-content: center;
      }
      #bsai-perf-panel.bsai-layout-h { width: auto !important; }
      #bsai-perf-panel .bsai-gauge {
        width: 92px; text-align: center; opacity: 1;
        transition: opacity 0.3s;
      }
      #bsai-perf-panel .bsai-gauge-svg-wrap { line-height: 0; }
      /* 指针旋转：围绕 SVG view-box 中心（59,59），纯 CSS 相对旋转（初始指向 -135° 刻度起点） */
      #bsai-perf-panel .bsai-gauge-ptr {
        transform-origin: 50% 50%;
        transform-box: view-box;
        transition: transform 0.45s cubic-bezier(0.34, 1.3, 0.64, 1);
        will-change: transform;
      }
      /* 指针拖尾光幕：弧形渐变光带（双层模糊，渐变到透明），随指针平滑旋转 */
      #bsai-perf-panel .bsai-gauge-veil {
        transform-origin: 50% 50%;
        transform-box: view-box;
        transition: transform 0.45s cubic-bezier(0.34, 1.3, 0.64, 1), opacity 0.45s ease;
        will-change: transform, opacity;
      }
      /* 拖动中禁用 backdrop-filter 与所有过渡（blur 每帧重算是卡顿主因） */
      #bsai-perf-panel.bsai-dragging { backdrop-filter: none; }
      #bsai-perf-panel.bsai-dragging * { transition: none !important; }
      #bsai-perf-panel .bsai-gauge-label {
        font-size: 11px; font-weight: 600; margin-top: 2px; color: ${THEME.text};
      }
      #bsai-perf-panel .bsai-gauge-sub {
        font-size: 8.5px; color: ${THEME.sub}; margin-top: 1px;
        font-family: Consolas, monospace; white-space: normal; line-height: 1.3;
        word-break: break-all;
      }
      #bsai-perf-panel .bsai-perf-footer {
        flex-basis: 100%; text-align: center;
        font-size: 9px; color: ${THEME.sub}; padding: 4px 2px 6px;
        border-top: 1px dashed rgba(255,255,255,0.1);
        font-family: Consolas, monospace;
        white-space: normal; line-height: 1.4;
      }
      /* 右键透明度菜单 */
      #bsai-perf-opmenu {
        position: fixed; z-index: 100000;
        background: rgba(16,18,28,0.96);
        border: 1px solid rgba(120,160,255,0.4);
        border-radius: 10px; padding: 10px 12px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.55), 0 0 12px rgba(90,140,255,0.15);
        color: ${THEME.text}; font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
        user-select: none; min-width: 168px;
      }
      #bsai-perf-opmenu .bsai-op-title {
        font-size: 12px; font-weight: 700; margin-bottom: 8px;
        background: linear-gradient(90deg, #4db8ff, #a06fff);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
      }
      #bsai-perf-opmenu input[type=range] {
        width: 100%; height: 4px; accent-color: #4db8ff; cursor: pointer;
      }
      #bsai-perf-opmenu .bsai-op-row {
        display: flex; align-items: center; justify-content: space-between;
        margin-top: 8px; gap: 8px;
      }
      #bsai-perf-opmenu #bsai-op-val {
        font-size: 12px; font-weight: 700; font-family: Consolas, monospace;
        color: #4db8ff; min-width: 38px;
      }
      #bsai-perf-opmenu .bsai-op-presets { display: flex; gap: 4px; }
      #bsai-perf-opmenu .bsai-op-presets button {
        background: rgba(255,255,255,0.1); border: none; color: ${THEME.text};
        border-radius: 4px; padding: 2px 6px; font-size: 10px; cursor: pointer; line-height: 1.3;
      }
      #bsai-perf-opmenu .bsai-op-presets button:hover { background: rgba(120,160,255,0.35); }
      #bsai-perf-opmenu .bsai-op-presets button.active {
        background: rgba(77,184,255,0.45); color: #fff; font-weight: 700;
      }
    `;
    document.head.appendChild(style);

    // ---------------- 注册扩展 + 多层自启动 ----------------
    // 新版 ComfyUI 前端只在其初始化阶段调度 extension.setup()；
    // 迟到注册的 extension 的 setup 不会被调用。因此采用多层保障：
    //   1) script 加载时 body 已就绪 -> 立即构建
    //   2) body 未就绪 -> DOMContentLoaded 时构建
    //   3) extension.setup() / beforeRegisterNodeDef 触发时兜底构建
    function ensurePanel() {
        if (document.getElementById("bsai-perf-panel")) return;
        try {
            buildPanel();
            start();
        } catch (e) {
            console.error("[BSAI-Perf] buildPanel failed:", e);
        }
    }

    if (document.body) {
        ensurePanel();
    } else {
        document.addEventListener("DOMContentLoaded", ensurePanel);
    }

    app.registerExtension({
        name: "BSAI.PerfDashboard",
        setup() {
            ensurePanel();
        },
        beforeRegisterNodeDef() {
            ensurePanel();
        },
    });
})();
