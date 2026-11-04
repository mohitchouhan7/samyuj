from __future__ import annotations

GTK_CSS_LIGHT = b"""
window, .sc-window, dialog, messagedialog, popover {
    background-color: #f6f9fc;
    color: #111820;
    font-family: "Inter", "Segoe UI", "Cantarell", "Ubuntu", sans-serif;
    font-size: 14px;
}
.sidebar {
    background-image: linear-gradient(155deg, #e8eff7 0%, #eef4fa 60%, #e6eef7 100%);
    border-right: 1px solid #d8e2ee;
    padding: 24px 13px 18px 13px;
}
.brand-title {
    font-size: 20px;
    font-weight: 700;
    color: #141b24;
    letter-spacing: -0.4px;
}
.nav-btn {
    background: transparent;
    border: none;
    border-radius: 15px;
    padding: 12px 20px;
    color: #1c2530;
    font-size: 17px;
    font-weight: 500;
    box-shadow: none;
}
.nav-btn:hover {
    background-color: #dfeaf5;
}
.nav-btn-active {
    background-color: #d2e3f8;
    color: #102238;
    font-weight: 700;
    border-left: 4px solid #2585ef;
}
.sidebar-divider {
    background-color: #cfd9e3;
    min-height: 1px;
    margin: 14px 12px 18px 12px;
}
.pair-qr-btn, .theme-toggle-btn {
    background-color: #ffffff;
    border: 1px solid #cbd5e0;
    border-radius: 13px;
    padding: 9px 14px;
    color: #1c2530;
    font-size: 13px;
    font-weight: 600;
}
.pair-qr-btn:hover, .theme-toggle-btn:hover {
    background-color: #e6f0fb;
    border-color: #2783e8;
    color: #176bd0;
}
.main-area, scrolledwindow, viewport {
    background-color: #f7f9fc;
    color: #111820;
}
.content-area {
    padding: 12px 26px 32px 26px;
}
.device-card {
    background-image: linear-gradient(115deg, #e5eef8 0%, #ebf3fc 55%, #e2ecf8 100%);
    border-radius: 22px;
    border: 1px solid #d5e2f0;
    padding: 24px 56px;
}
.device-heading {
    font-size: 34px;
    font-weight: 700;
    color: #0d141d;
    letter-spacing: -0.6px;
}
.conn-pill-connected {
    background-color: #daf2e5;
    color: #116b2b;
    border: 1px solid #b6e3cb;
    border-radius: 999px;
    padding: 6px 15px;
    font-size: 16px;
    font-weight: 600;
}
.conn-pill-waiting {
    background-color: #fff2d6;
    color: #94530d;
    border: 1px solid #f7d99c;
    border-radius: 999px;
    padding: 6px 15px;
    font-size: 15px;
    font-weight: 600;
}
.metric-text {
    font-size: 18px;
    color: #18212b;
    font-weight: 500;
}
.sc-card {
    background-color: #ffffff;
    border: 1px solid #dbe4ef;
    border-radius: 18px;
    padding: 16px 18px;
}
.panel-h2 {
    font-size: 16px;
    font-weight: 700;
    color: #162130;
}
.panel-sub {
    font-size: 12.5px;
    color: #5d6b7c;
}
.eyebrow {
    font-size: 11px;
    font-weight: 700;
    color: #2585ef;
    letter-spacing: 0.8px;
}
.page-h1 {
    font-size: 28px;
    font-weight: 700;
    color: #0f1722;
}
.page-sub {
    font-size: 13px;
    color: #5d6b7c;
}
.feature-rail-item {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 13px;
    padding: 7px 10px;
}
.feature-rail-item:hover {
    background-color: #eef4fb;
    border-color: #d6e3f2;
}
.feature-rail-item-active {
    background-color: #e4f5ea;
    border: 1px solid #28a66b;
    border-radius: 13px;
    padding: 7px 10px;
}
.feature-rail-title {
    font-size: 13px;
    font-weight: 600;
    color: #1c2635;
}
.feature-rail-title-active {
    font-size: 13px;
    font-weight: 700;
    color: #13752e;
}
.feature-rail-state {
    font-size: 11px;
    font-weight: 600;
    color: #16803b;
}
.feature-tile-card {
    background-image: linear-gradient(145deg, #ffffff 0%, #f5f9fe 100%);
    border: 1px solid #dbe4ef;
    border-radius: 18px;
    padding: 18px 14px;
}
.feature-tile-card:hover {
    border-color: #7ab3f0;
    background-image: linear-gradient(145deg, #ffffff 0%, #eaf3fd 100%);
}
.feature-tile-card-active {
    background-image: linear-gradient(145deg, #e7f6ed 0%, #dcf0e4 100%);
    border: 2px solid #229453;
    border-radius: 18px;
    padding: 18px 14px;
}
.feature-tile-title {
    font-size: 16px;
    font-weight: 600;
    color: #101720;
}
.feature-tile-title-active {
    font-size: 16px;
    font-weight: 700;
    color: #126b34;
}
.corner-cancel-btn {
    background-color: #fbe9e9;
    color: #c92a2a;
    border: 1px solid #f5c2c7;
    border-radius: 14px;
    min-width: 24px;
    min-height: 24px;
    padding: 0px 6px;
    font-size: 14px;
    font-weight: 700;
    box-shadow: none;
}
.corner-cancel-btn:hover {
    background-color: #e03131;
    color: #ffffff;
    border-color: #e03131;
}
.clear-btn {
    background: transparent;
    border: none;
    border-radius: 14px;
    padding: 5px 10px;
    color: #2783e8;
    font-size: 12px;
    font-weight: 600;
}
.clear-btn:hover {
    background-color: #e8f1fc;
}
.notif-row {
    border-bottom: 1px solid #edf1f6;
    padding: 9px 6px;
}
.notif-title {
    font-size: 13px;
    font-weight: 600;
    color: #1e293b;
}
.notif-body {
    font-size: 12px;
    color: #5d6b7c;
}
.notif-time {
    font-size: 11px;
    color: #8895a7;
}
.dropzone {
    background-color: #f8fbff;
    border: 2px dashed #b2c5db;
    border-radius: 18px;
    padding: 28px 20px;
}
.dropzone:hover {
    background-color: #ebf4ff;
    border-color: #2585ed;
}
.dropzone-title {
    font-size: 17px;
    font-weight: 700;
    color: #141e2b;
}
.dropzone-sub {
    font-size: 13px;
    color: #5d6b7c;
}
.primary-btn {
    background-color: #2585ef;
    background-image: none;
    color: #ffffff;
    border: 1px solid #1b74d8;
    border-radius: 13px;
    padding: 9px 16px;
    font-size: 13px;
    font-weight: 600;
}
.primary-btn:hover {
    background-color: #176bd0;
}
.secondary-btn {
    background-color: #ffffff;
    background-image: none;
    color: #253242;
    border: 1px solid #d1dce8;
    border-radius: 13px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
}
.secondary-btn:hover {
    background-color: #eef4fb;
    border-color: #90bcf0;
    color: #155ec0;
}
.danger-btn {
    background-color: #fff5f5;
    color: #c92a2a;
    border: 1px solid #ffc9c9;
    border-radius: 13px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
}
.danger-btn:hover {
    background-color: #e03131;
    color: #ffffff;
}
.shared-badge {
    background-color: #e6f6ed;
    color: #157a38;
    border: 1px solid #bce6cd;
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}
.sent-badge {
    background-color: #e6f0fb;
    color: #1c6cd5;
    border: 1px solid #bfd8f6;
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}
.phonefs-scroll {
    background-color: #ffffff;
    border: 1px solid #d8e2ee;
    border-radius: 14px;
}
.phonefs-scroll viewport {
    background-color: #ffffff;
}
treeview.view, .phonefs-treeview {
    background-color: #ffffff;
    color: #182230;
    font-size: 13.5px;
    padding: 6px 8px;
}
treeview.view:hover, .phonefs-treeview:hover {
    background-color: #f1f6fd;
    color: #0f1722;
}
treeview.view:selected, .phonefs-treeview:selected {
    background-color: #2585ef;
    color: #ffffff;
}
treeview.view header button {
    background-color: #f0f5fb;
    background-image: none;
    color: #475569;
    font-weight: 700;
    font-size: 12px;
    border: none;
    border-bottom: 1px solid #d8e2ee;
    border-right: 1px solid #e6edf5;
    padding: 8px 10px;
}
treeview.view header button:hover {
    background-color: #e4eef9;
    color: #1e293b;
}
.quick-path-pill {
    background-color: #f0f5fb;
    background-image: none;
    color: #25384e;
    border: 1px solid #d6e2f0;
    border-radius: 999px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
}
button.gallery-photo-tile {
    padding: 0px;
    margin: 0px;
    min-width: 128px;
    min-height: 96px;
    border-radius: 14px;
    background-color: #0f172a;
    background-image: none;
    border: 1px solid rgba(148, 163, 184, 0.25);
}
button.gallery-photo-tile:hover {
    border-color: #2585ef;
    background-color: #1e293b;
}
.quick-path-pill:hover {
    background-color: #dcebfa;
    border-color: #2585ef;
    color: #1459b8;
}
entry, .sc-entry {
    background-color: #ffffff;
    background-image: none;
    color: #182230;
    border: 1px solid #cbd6e2;
    border-radius: 13px;
    padding: 7px 12px;
    caret-color: #2585ef;
}
entry:focus, .sc-entry:focus {
    border-color: #2585ef;
}
combobox button.combo {
    background-color: #ffffff;
    background-image: none;
    color: #182230;
    border: 1px solid #cbd6e2;
    border-radius: 13px;
    padding: 6px 12px;
}
combobox button.combo:hover {
    background-color: #f0f5fb;
    border-color: #2585ef;
}
menu, .context-menu {
    background-color: #ffffff;
    color: #182230;
    border: 1px solid #d8e2ee;
}
menuitem {
    color: #182230;
    padding: 6px 12px;
}
menuitem:hover {
    background-color: #e6f0fb;
    color: #1459b8;
}
.edge-hud-pill {
    background-color: #111926;
    color: #ffffff;
    border: 1px solid #2783e8;
    border-radius: 14px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
}
.fw-toolbar {
    background-color: #fbfcfe;
    border-bottom: 1px solid #dfe5ec;
    padding: 2px 6px;
}
.fw-headerbar {
    background-color: #111822;
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
    background-color: #111822;
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
    background-color: #ffffff;
    color: #1c2530;
    border: 1px solid #d5dfe9;
    border-radius: 14px;
    padding: 6px 12px;
    font-size: 15px;
    font-weight: 700;
}
.setup-back-btn:hover {
    background-color: #e8f1fc;
    border-color: #2783e8;
    color: #176bd0;
}
.setup-info-btn {
    background-color: #ffffff;
    color: #2783e8;
    border: 1px solid #d5dfe9;
    border-radius: 999px;
    min-width: 32px;
    min-height: 32px;
    padding: 4px 8px;
    font-size: 14px;
    font-weight: 700;
}
.setup-info-btn:hover {
    background-color: #e8f1fc;
    border-color: #2783e8;
}
.setup-option-card {
    background-color: #ffffff;
    border: 1px solid #dbe3ec;
    border-radius: 16px;
    padding: 16px 18px;
}
.setup-option-card:hover {
    background-color: #f4f8fd;
    border-color: #9ec3ef;
}
.setup-option-card-selected {
    background-color: #eef5fc;
    border: 2px solid #2585ef;
    border-radius: 16px;
    padding: 16px 18px;
}
.setup-option-title {
    font-size: 15px;
    font-weight: 700;
    color: #101720;
}
.setup-option-sub {
    font-size: 12.5px;
    color: #5b6877;
}
.setup-start-btn {
    background-color: #2783e8;
    color: #ffffff;
    border: none;
    border-radius: 14px;
    padding: 11px 28px;
    font-size: 14px;
    font-weight: 700;
}
.setup-start-btn:hover {
    background-color: #176bd0;
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
