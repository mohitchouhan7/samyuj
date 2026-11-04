from __future__ import annotations

GTK_CSS_DARK = b"""
window, .sc-window, dialog, messagedialog, popover {
    background-color: #0e1522;
    color: #e8eff8;
    font-family: "Inter", "Segoe UI", "Cantarell", "Ubuntu", sans-serif;
    font-size: 14px;
}
.sidebar {
    background-image: linear-gradient(155deg, #111927 0%, #152033 60%, #0f1724 100%);
    border-right: 1px solid #223148;
    padding: 24px 13px 18px 13px;
}
.brand-title {
    font-size: 20px;
    font-weight: 700;
    color: #f8fafc;
    letter-spacing: -0.4px;
}
.nav-btn {
    background: transparent;
    border: none;
    border-radius: 15px;
    padding: 12px 20px;
    color: #cbd5e1;
    font-size: 17px;
    font-weight: 500;
    box-shadow: none;
}
.nav-btn:hover {
    background-color: #1b283d;
    color: #f8fafc;
}
.nav-btn-active {
    background-color: #1e3252;
    color: #ffffff;
    font-weight: 700;
    border-left: 4px solid #60a5fa;
}
.sidebar-divider {
    background-color: #233248;
    min-height: 1px;
    margin: 14px 12px 18px 12px;
}
.pair-qr-btn, .theme-toggle-btn {
    background-color: #182336;
    border: 1px solid #2b3d59;
    border-radius: 13px;
    padding: 9px 14px;
    color: #e2e8f0;
    font-size: 13px;
    font-weight: 600;
}
.pair-qr-btn:hover, .theme-toggle-btn:hover {
    background-color: #223452;
    border-color: #60a5fa;
    color: #ffffff;
}
.main-area, scrolledwindow, viewport {
    background-color: #0e1522;
    color: #e8eff8;
}
.content-area {
    padding: 12px 26px 32px 26px;
}
.device-card {
    background-image: linear-gradient(115deg, #162236 0%, #1b2b44 55%, #141f32 100%);
    border-radius: 22px;
    border: 1px solid #283c59;
    padding: 24px 56px;
}
.device-heading {
    font-size: 34px;
    font-weight: 700;
    color: #f8fafc;
    letter-spacing: -0.6px;
}
.conn-pill-connected {
    background-color: #123628;
    color: #4ade80;
    border: 1px solid #1e5c42;
    border-radius: 999px;
    padding: 6px 15px;
    font-size: 16px;
    font-weight: 600;
}
.conn-pill-waiting {
    background-color: #382814;
    color: #fbbf24;
    border: 1px solid #6b4c23;
    border-radius: 999px;
    padding: 6px 15px;
    font-size: 15px;
    font-weight: 600;
}
.metric-text {
    font-size: 18px;
    color: #e2e8f0;
    font-weight: 500;
}
.sc-card {
    background-color: #151f2f;
    border: 1px solid #24344d;
    border-radius: 18px;
    padding: 16px 18px;
}
.panel-h2 {
    font-size: 16px;
    font-weight: 700;
    color: #f1f5f9;
}
.panel-sub {
    font-size: 12.5px;
    color: #94a3b8;
}
.eyebrow {
    font-size: 11px;
    font-weight: 700;
    color: #60a5fa;
    letter-spacing: 0.8px;
}
.page-h1 {
    font-size: 28px;
    font-weight: 700;
    color: #f8fafc;
}
.page-sub {
    font-size: 13px;
    color: #94a3b8;
}
.feature-rail-item {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 13px;
    padding: 7px 10px;
}
.feature-rail-item:hover {
    background-color: #1c293e;
    border-color: #2b3f5c;
}
.feature-rail-item-active {
    background-color: #123327;
    border: 1px solid #22c55e;
    border-radius: 13px;
    padding: 7px 10px;
}
.feature-rail-title {
    font-size: 13px;
    font-weight: 600;
    color: #e2e8f0;
}
.feature-rail-title-active {
    font-size: 13px;
    font-weight: 700;
    color: #4ade80;
}
.feature-rail-state {
    font-size: 11px;
    font-weight: 600;
    color: #4ade80;
}
.feature-tile-card {
    background-image: linear-gradient(145deg, #182334 0%, #141d2c 100%);
    border: 1px solid #26364f;
    border-radius: 18px;
    padding: 18px 14px;
}
.feature-tile-card:hover {
    border-color: #60a5fa;
    background-image: linear-gradient(145deg, #1d2b40 0%, #172335 100%);
}
.feature-tile-card-active {
    background-image: linear-gradient(145deg, #133327 0%, #102920 100%);
    border: 2px solid #22c55e;
    border-radius: 18px;
    padding: 18px 14px;
}
.feature-tile-title {
    font-size: 16px;
    font-weight: 600;
    color: #f1f5f9;
}
.feature-tile-title-active {
    font-size: 16px;
    font-weight: 700;
    color: #4ade80;
}
.corner-cancel-btn {
    background-color: #361a21;
    color: #fca5a5;
    border: 1px solid #5c2833;
    border-radius: 14px;
    min-width: 24px;
    min-height: 24px;
    padding: 0px 6px;
    font-size: 14px;
    font-weight: 700;
    box-shadow: none;
}
.corner-cancel-btn:hover {
    background-color: #ef4444;
    color: #ffffff;
    border-color: #ef4444;
}
.clear-btn {
    background: transparent;
    border: none;
    border-radius: 14px;
    padding: 5px 10px;
    color: #60a5fa;
    font-size: 12px;
    font-weight: 600;
}
.clear-btn:hover {
    background-color: #1e3252;
}
.notif-row {
    border-bottom: 1px solid #1f2d42;
    padding: 9px 6px;
}
.notif-title {
    font-size: 13px;
    font-weight: 600;
    color: #e2e8f0;
}
.notif-body {
    font-size: 12px;
    color: #94a3b8;
}
.notif-time {
    font-size: 11px;
    color: #64748b;
}
.dropzone {
    background-color: #121b2a;
    border: 2px dashed #334968;
    border-radius: 18px;
    padding: 28px 20px;
}
.dropzone:hover {
    background-color: #18253a;
    border-color: #60a5fa;
}
.dropzone-title {
    font-size: 17px;
    font-weight: 700;
    color: #f1f5f9;
}
.dropzone-sub {
    font-size: 13px;
    color: #94a3b8;
}
.primary-btn {
    background-color: #2563eb;
    background-image: none;
    color: #ffffff;
    border: 1px solid #3b82f6;
    border-radius: 13px;
    padding: 9px 16px;
    font-size: 13px;
    font-weight: 600;
}
.primary-btn:hover {
    background-color: #3b82f6;
}
.secondary-btn {
    background-color: #1c283b;
    background-image: none;
    color: #e2e8f0;
    border: 1px solid #2d3f5b;
    border-radius: 13px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
}
.secondary-btn:hover {
    background-color: #253650;
    border-color: #60a5fa;
    color: #ffffff;
}
.danger-btn {
    background-color: #361a21;
    color: #fca5a5;
    border: 1px solid #5c2833;
    border-radius: 13px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
}
.danger-btn:hover {
    background-color: #ef4444;
    color: #ffffff;
}
.shared-badge {
    background-color: #123628;
    color: #4ade80;
    border: 1px solid #1e5c42;
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}
.sent-badge {
    background-color: #172e4d;
    color: #60a5fa;
    border: 1px solid #264a78;
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}
.phonefs-scroll {
    background-color: #111926;
    border: 1px solid #25354d;
    border-radius: 14px;
}
.phonefs-scroll viewport {
    background-color: #111926;
}
treeview.view, .phonefs-treeview {
    background-color: #111926;
    color: #e2e8f0;
    font-size: 13.5px;
    padding: 6px 8px;
}
treeview.view:hover, .phonefs-treeview:hover {
    background-color: #1a273b;
    color: #ffffff;
}
treeview.view:selected, .phonefs-treeview:selected {
    background-color: #2563eb;
    color: #ffffff;
}
treeview.view header button {
    background-color: #182334;
    background-image: none;
    color: #94a3b8;
    font-weight: 700;
    font-size: 12px;
    border: none;
    border-bottom: 1px solid #25354d;
    border-right: 1px solid #1f2d42;
    padding: 8px 10px;
}
treeview.view header button:hover {
    background-color: #213047;
    color: #f1f5f9;
}
.quick-path-pill {
    background-color: #1a273b;
    background-image: none;
    color: #cbd5e1;
    border: 1px solid #2b3d59;
    border-radius: 999px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
}
.quick-path-pill:hover {
    background-color: #243754;
    border-color: #60a5fa;
    color: #ffffff;
}
entry, .sc-entry {
    background-color: #111926;
    background-image: none;
    color: #f1f5f9;
    border: 1px solid #2d3f5b;
    border-radius: 13px;
    padding: 7px 12px;
    caret-color: #60a5fa;
}
entry:focus, .sc-entry:focus {
    border-color: #60a5fa;
}
combobox button.combo {
    background-color: #182334;
    background-image: none;
    color: #f1f5f9;
    border: 1px solid #2d3f5b;
    border-radius: 13px;
    padding: 6px 12px;
}
combobox button.combo:hover {
    background-color: #22324a;
    border-color: #60a5fa;
}
menu, .context-menu {
    background-color: #151f2f;
    color: #e8eff8;
    border: 1px solid #283a54;
}
menuitem {
    color: #e8eff8;
    padding: 6px 12px;
}
menuitem:hover {
    background-color: #1e3252;
    color: #ffffff;
}
.edge-hud-pill {
    background-color: #111926;
    color: #ffffff;
    border: 1px solid #60a5fa;
    border-radius: 14px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
}
.fw-toolbar {
    background-color: #111822;
    border-bottom: 1px solid #1e2938;
    padding: 2px 6px;
}
.fw-headerbar {
    background-color: #0b1018;
    padding: 0px 2px;
}
.fw-headerbar button {
    min-width: 16px;
    min-height: 20px;
    padding: 1px 4px;
    margin: 0px 1px;
    color: #eef3f9;
    border-bottom: 1px solid #1e2938;
    min-height: 32px;
    padding: 1px 6px;
}
.fw-headerbar label {
    color: #e8eff7;
    font-size: 12px;
}
.fw-headerbar .fw-title {
    font-weight: 700;
    font-size: 12.5px;
    color: #ffffff;
}
.fw-headerbar .fw-sub {
    font-size: 11px;
    color: #9cb0c9;
}
.fw-ctrl-btn {
    background-color: #1c2736;
    color: #e8eff7;
    border: 1px solid #2e3e54;
    border-radius: 14px;
    padding: 2px 7px;
    min-height: 22px;
    min-width: 24px;
    font-size: 11.5px;
    font-weight: 600;
}
.fw-ctrl-btn:hover {
    background-color: #263549;
}
.fw-end-btn {
    background-color: #c93535;
    color: #ffffff;
    border: 1px solid #e04a4a;
    border-radius: 14px;
    padding: 2px 8px;
    min-height: 22px;
    font-size: 11.5px;
    font-weight: 700;
}
.fw-video-canvas {
    background-color: #000000;
    margin: 0px;
    padding: 0px;
}
.fw-navbar {
    background-color: #0b1018;
    border-top: 1px solid #1e2938;
    padding: 4px 8px;
}
.fw-nav-btn {
    background-color: #1c2736;
    color: #e8eff7;
    border: 1px solid #2e3e54;
    border-radius: 14px;
    padding: 3px 14px;
    min-height: 26px;
    min-width: 44px;
    font-size: 13px;
    font-weight: 700;
}
.fw-nav-btn:hover {
    background-color: #2783e8;
    border-color: #4da3ff;
    color: #ffffff;
}
.setup-header-bar {
    padding: 4px 0px 8px 0px;
}
.setup-back-btn {
    background-color: #182334;
    color: #e2e8f0;
    border: 1px solid #2b3d59;
    border-radius: 14px;
    padding: 6px 12px;
    font-size: 15px;
    font-weight: 700;
}
.setup-back-btn:hover {
    background-color: #223452;
    border-color: #60a5fa;
    color: #ffffff;
}
.setup-info-btn {
    background-color: #182334;
    color: #60a5fa;
    border: 1px solid #2b3d59;
    border-radius: 999px;
    min-width: 32px;
    min-height: 32px;
    padding: 4px 8px;
    font-size: 14px;
    font-weight: 700;
}
.setup-info-btn:hover {
    background-color: #223452;
    border-color: #60a5fa;
}
.setup-option-card {
    background-color: #151f2f;
    border: 1px solid #25354d;
    border-radius: 16px;
    padding: 16px 18px;
}
.setup-option-card:hover {
    background-color: #1b283d;
    border-color: #60a5fa;
}
.setup-option-card-selected {
    background-color: #1a2d49;
    border: 2px solid #3b82f6;
    border-radius: 16px;
    padding: 16px 18px;
}
.setup-option-title {
    font-size: 15px;
    font-weight: 700;
    color: #f8fafc;
}
.setup-option-sub {
    font-size: 12.5px;
    color: #94a3b8;
}
.setup-start-btn {
    background-color: #2563eb;
    color: #ffffff;
    border: none;
    border-radius: 14px;
    padding: 11px 28px;
    font-size: 14px;
    font-weight: 700;
}
.setup-start-btn:hover {
    background-color: #3b82f6;
}

button, togglebutton, spinbutton {
    border-radius: 12px;
}
frame, frame > border {
    border-radius: 18px;
}
popover, menu, .context-menu {
    border-radius: 14px;
    padding: 4px;
}
menuitem {
    border-radius: 9px;
}
scrollbar slider, switch, switch slider, radiobutton radio {
    border-radius: 999px;
}
checkbutton check {
    border-radius: 6px;
}
"""
