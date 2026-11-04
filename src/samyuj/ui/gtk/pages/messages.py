from __future__ import annotations

import contextlib

from pathlib import Path

from samyuj.ui.gtk.gtk_compat import Gdk, GdkPixbuf, GLib, Gtk, Pango

class MessagesPageMixin:

    def _build_messages_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._selected_msg_thread_id: str | None = None

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        h_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        eb = Gtk.Label(label="ANDROID SMS / MMS (SVC-204)", xalign=0.0)
        eb.get_style_context().add_class("eyebrow")
        h1 = Gtk.Label(label="Messages", xalign=0.0)
        h1.get_style_context().add_class("page-h1")
        self.messages_status_lbl = Gtk.Label(
            label="Read and send SMS/MMS messages via your connected Android phone.",
            xalign=0.0,
        )
        self.messages_status_lbl.get_style_context().add_class("page-sub")
        h_left.pack_start(eb, False, False, 0)
        h_left.pack_start(h1, False, False, 0)
        h_left.pack_start(self.messages_status_lbl, False, False, 0)
        head.pack_start(h_left, True, True, 0)

        self.messages_perm_btn = Gtk.Button(label="Grant Permission on Phone")
        self.messages_perm_btn.get_style_context().add_class("secondary-btn")
        self.messages_perm_btn.set_valign(Gtk.Align.START)
        self.messages_perm_btn.connect("clicked", lambda *_: self._on_messages_permission_clicked())
        head.pack_end(self.messages_perm_btn, False, False, 0)

        sync_btn = Gtk.Button(label="Sync Inbox")
        sync_btn.get_style_context().add_class("primary-btn")
        sync_btn.set_valign(Gtk.Align.START)
        sync_btn.connect("clicked", lambda *_: self._on_messages_sync_clicked())
        head.pack_end(sync_btn, False, False, 0)
        vbox.pack_start(head, False, False, 0)

        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.messages_search_entry = Gtk.Entry()
        self.messages_search_entry.get_style_context().add_class("sc-entry")
        self.messages_search_entry.set_placeholder_text("Search messages or contacts…")
        self.messages_search_entry.connect("activate", lambda *_: self._on_messages_search_clicked())
        self.messages_search_entry.connect("changed", lambda *_: self._on_messages_search_clicked())
        top_bar.pack_start(self.messages_search_entry, True, True, 0)

        search_btn = Gtk.Button(label="Search")
        search_btn.get_style_context().add_class("secondary-btn")
        search_btn.connect("clicked", lambda *_: self._on_messages_search_clicked())
        top_bar.pack_start(search_btn, False, False, 0)

        self.messages_sim_combo = Gtk.ComboBoxText()
        self.messages_sim_combo.append("-1", "Default SIM (subId -1)")
        self.messages_sim_combo.set_active_id("-1")
        self.messages_sim_combo.connect(
            "changed",
            lambda cb: self._spawn(self.facade.request(f"messages-select-sim:{cb.get_active_id() or '-1'}"))
            if not getattr(self, "_updating_sim_combo", False) else None,
        )
        top_bar.pack_end(self.messages_sim_combo, False, False, 0)
        vbox.pack_start(top_bar, False, False, 0)

        split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        vbox.pack_start(split, True, True, 0)

        left_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        left_card.set_size_request(290, 360)
        left_card.get_style_context().add_class("sc-card")
        l_hdr = Gtk.Label(label="Conversations", xalign=0.0)
        l_hdr.get_style_context().add_class("panel-h2")
        left_card.pack_start(l_hdr, False, False, 0)

        conv_scroll = Gtk.ScrolledWindow()
        with contextlib.suppress(Exception):
            from samyuj.ui.gtk.smooth_scroll import attach_smooth_scroll
            attach_smooth_scroll(conv_scroll)
        conv_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        conv_scroll.set_vexpand(True)
        self.messages_conv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        conv_scroll.add(self.messages_conv_box)
        left_card.pack_start(conv_scroll, True, True, 0)
        split.pack_start(left_card, False, False, 0)

        right_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right_card.get_style_context().add_class("sc-card")
        r_hdr_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.messages_thread_title = Gtk.Label(label="Thread Messages", xalign=0.0)
        self.messages_thread_title.get_style_context().add_class("panel-h2")
        r_hdr_row.pack_start(self.messages_thread_title, True, True, 0)
        mark_read_btn = Gtk.Button(label="Mark Read")
        mark_read_btn.get_style_context().add_class("secondary-btn")
        mark_read_btn.connect("clicked", lambda *_: self._on_messages_mark_read_clicked())
        r_hdr_row.pack_end(mark_read_btn, False, False, 0)
        right_card.pack_start(r_hdr_row, False, False, 0)

        thread_scroll = Gtk.ScrolledWindow()
        with contextlib.suppress(Exception):
            from samyuj.ui.gtk.smooth_scroll import attach_smooth_scroll
            attach_smooth_scroll(thread_scroll)
        thread_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        thread_scroll.set_vexpand(True)
        self.messages_thread_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        thread_scroll.add(self.messages_thread_box)
        right_card.pack_start(thread_scroll, True, True, 0)

        comp_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.messages_to_entry = Gtk.Entry()
        self.messages_to_entry.get_style_context().add_class("sc-entry")
        self.messages_to_entry.set_placeholder_text("Recipient (+1555…)")
        self.messages_to_entry.set_width_chars(18)
        comp_row.pack_start(self.messages_to_entry, False, False, 0)

        self.messages_body_entry = Gtk.Entry()
        self.messages_body_entry.get_style_context().add_class("sc-entry")
        self.messages_body_entry.set_placeholder_text("Type an SMS message…")
        self.messages_body_entry.connect("activate", lambda *_: self._on_messages_send_clicked())
        comp_row.pack_start(self.messages_body_entry, True, True, 0)

        send_sms_btn = Gtk.Button(label="Send SMS")
        send_sms_btn.get_style_context().add_class("primary-btn")
        send_sms_btn.connect("clicked", lambda *_: self._on_messages_send_clicked())
        comp_row.pack_start(send_sms_btn, False, False, 0)
        right_card.pack_end(comp_row, False, False, 0)

        split.pack_start(right_card, True, True, 0)
        return vbox

    def _on_messages_updated(self) -> bool:
        self._refresh_messages_page(force=True)
        return False

    def _on_messages_permission_clicked(self) -> None:
        self.messages_status_lbl.set_text("Requesting SMS permission on your phone — check your phone screen and tap Allow…")
        self._spawn(self.facade.request("messages-permission"))
        GLib.timeout_add(450, lambda: (self._refresh_messages_page(force=True), False)[1])

    def _on_messages_sync_clicked(self) -> None:
        self.messages_status_lbl.set_text("Syncing contacts, SMS/MMS threads, and SIM cards from phone…")
        self._spawn(self.facade.request("messages-sync"))
        GLib.timeout_add(350, lambda: (self._refresh_messages_page(force=True), False)[1])

    def _on_messages_search_clicked(self) -> None:
        kw = (self.messages_search_entry.get_text() or "").strip()
        self.facade.messages_state["search_query"] = kw
        self._refresh_messages_page(force=True)

    def _on_messages_mark_read_clicked(self) -> None:
        tid = getattr(self, "_selected_msg_thread_id", None)
        if tid is not None:
            self._spawn(self.facade.request(f"messages-read:{tid}"))
            GLib.timeout_add(250, lambda: (self._refresh_messages_page(force=True), False)[1])

    def _on_messages_send_clicked(self) -> None:
        addr = (self.messages_to_entry.get_text() or "").strip()
        body = (self.messages_body_entry.get_text() or "").strip()
        sub_id = (self.messages_sim_combo.get_active_id() or "-1").strip()
        if not addr or not body:
            self.messages_status_lbl.set_text("Enter both recipient number and message text.")
            return
        self.messages_body_entry.set_text("")
        self._spawn(self.facade.request(f"messages-send:{addr}:{body}:{sub_id}"))
        self.messages_status_lbl.set_text(f"Sending SMS to {addr} (SIM subId={sub_id})…")
        GLib.timeout_add(200, lambda: (self._refresh_messages_page(force=True), False)[1])

    def _refresh_messages_page(self, force: bool = False) -> None:
        conv_box = getattr(self, "messages_conv_box", None)
        thread_box = getattr(self, "messages_thread_box", None)
        if conv_box is None or thread_box is None:
            return
        st = getattr(self.facade, "messages_state", {}) or {}
        sim_cards = list(st.get("simCards") or st.get("sim_cards") or [])
        convs = dict(st.get("threads") or st.get("conversations") or {})
        contacts = list(st.get("contacts") or [])
        search_q = str(st.get("search_query") or "").strip().lower()
        granted = bool(st.get("granted", False))
        enabled = bool(st.get("enabled", False))

        contact_map: dict[str, str] = {}
        for c_item in contacts:
            if isinstance(c_item, dict):
                cnum = str(c_item.get("number") or "").strip()
                cname = str(c_item.get("name") or "").strip()
                if cnum and cname:
                    contact_map[cnum] = cname
                    clean_num = "".join(ch for ch in cnum if ch.isdigit())
                    if len(clean_num) >= 7:
                        contact_map[clean_num[-10:]] = cname

        def _display_peer(peer_addr: str) -> str:
            p_str = str(peer_addr or "").strip()
            if p_str in contact_map:
                return f"{contact_map[p_str]} ({p_str})"
            clean_p = "".join(ch for ch in p_str if ch.isdigit())
            if len(clean_p) >= 7 and clean_p[-10:] in contact_map:
                return f"{contact_map[clean_p[-10:]]} ({p_str})"
            return p_str or "Unknown"

        sig = (
            tuple(sorted(convs.keys())),
            len(st.get("messages") or []),
            tuple(s.get("subId") for s in sim_cards if isinstance(s, dict)),
            len(contacts),
            granted,
            enabled,
            search_q,
            getattr(self, "_selected_msg_thread_id", None),
        )
        if not force and getattr(self, "_last_messages_sig", None) == sig:
            return
        self._last_messages_sig = sig

        if getattr(self, "messages_perm_btn", None) is not None:
            self.messages_perm_btn.set_visible(not granted)

        if getattr(self, "messages_status_lbl", None) is not None:
            if self.facade._live_ws() is None and not convs:
                self.messages_status_lbl.set_text("Connect your Android phone and click 'Sync Inbox' to load SMS/MMS conversations.")
            elif not granted and not convs:
                self.messages_status_lbl.set_text("SMS permission needed on phone — click 'Grant Permission on Phone' or 'Sync Inbox' and tap Allow on your phone.")
            else:
                self.messages_status_lbl.set_text(
                    f"Synced: {len(convs)} conversation(s) · {len(st.get('messages') or [])} message(s) · "
                    f"{len(contacts)} contact(s) · Unread: {st.get('unread', 0)}"
                )

        if getattr(self, "messages_sim_combo", None) is not None:
            self._updating_sim_combo = True
            try:
                cur_sim = str(st.get("selected_sub_id", -1))
                if cur_sim == "-1":
                    cur_sim = self.messages_sim_combo.get_active_id() or "-1"
                self.messages_sim_combo.remove_all()
                self.messages_sim_combo.append("-1", "Default SIM (subId -1)")
                for sc in sim_cards:
                    if not isinstance(sc, dict):
                        continue
                    sid_str = str(sc.get("subId", -1))
                    slot_id = int(sc.get("slotId", 0) or 0) + 1
                    pnum = str(sc.get("phoneNumber") or sc.get("carrierName") or sc.get("displayName") or f"SIM {slot_id}")
                    self.messages_sim_combo.append(sid_str, f"SIM {slot_id}: {pnum} (subId {sid_str})")
                if not self.messages_sim_combo.set_active_id(cur_sim):
                    self.messages_sim_combo.set_active_id("-1")
            finally:
                self._updating_sim_combo = False

        for ch in list(conv_box.get_children()):
            conv_box.remove(ch)
        for ch in list(thread_box.get_children()):
            thread_box.remove(ch)

        sorted_peers = sorted(
            convs.keys(),
            key=lambda k: max((int(m.get("date", 0) or 0) for m in (convs.get(k) or [])), default=0),
            reverse=True,
        )
        if search_q:
            filtered_peers = []
            for pk in sorted_peers:
                disp_p = _display_peer(pk).lower()
                t_list = convs.get(pk) or []
                if search_q in disp_p or any(search_q in str(m.get("body") or "").lower() for m in t_list):
                    filtered_peers.append(pk)
            sorted_peers = filtered_peers

        if not sorted_peers:
            emp = Gtk.Label(
                label="No conversations synced yet — click 'Sync Inbox' (and accept the SMS permission prompt on your phone if shown).",
                xalign=0.0,
            )
            emp.get_style_context().add_class("panel-sub")
            emp.set_line_wrap(True)
            conv_box.pack_start(emp, False, False, 0)
        else:
            if self._selected_msg_thread_id not in convs:
                self._selected_msg_thread_id = sorted_peers[0]
            for tid in sorted_peers:
                msgs = convs.get(tid) or []
                last_m = msgs[-1] if msgs else {}
                peer_label = _display_peer(tid)
                snippet = str(last_m.get("body") or "[MMS Media]").splitlines()[0][:38] if str(last_m.get("body") or "[MMS Media]") else "" 
                unread_in_t = sum(1 for m in msgs if int(m.get("read", 1) or 0) == 0)
                badge_prefix = f"({unread_in_t}) " if unread_in_t > 0 else ""
                btn = Gtk.Button(label=f"{badge_prefix}{peer_label}: {snippet}")
                btn.get_style_context().add_class("primary-btn" if tid == self._selected_msg_thread_id else "secondary-btn")

                def _sel_t(_b, t_id=tid):
                    self._selected_msg_thread_id = t_id
                    self.messages_to_entry.set_text(str(t_id))
                    self._refresh_messages_page(force=True)

                btn.connect("clicked", _sel_t)
                conv_box.pack_start(btn, False, False, 0)

        sel_tid = self._selected_msg_thread_id
        if getattr(self, "messages_thread_title", None) is not None:
            self.messages_thread_title.set_text(
                f"Conversation with {_display_peer(sel_tid)}" if sel_tid else "Thread Messages"
            )
        if sel_tid and not self.messages_to_entry.get_text():
            self.messages_to_entry.set_text(str(sel_tid))

        t_msgs = list(convs.get(sel_tid) or [])
        if search_q:
            t_msgs = [m for m in t_msgs if search_q in str(m.get("body") or "").lower() or search_q in _display_peer(sel_tid).lower()]

        for m in t_msgs[-40:]:
            mid = str(m.get("msgId") or m.get("id") or "")
            mbody = str(m.get("body") or "")
            medias = list(m.get("medias") or [])
            is_mms = bool(int(m.get("messageType", 1) or 1) >= 2 or medias or m.get("has_mms_placeholder") or m.get("is_mms"))
            is_out = bool(m.get("outgoing") or (not m.get("fromAddress") and m.get("toAddress")))
            sender_tag = "You" if is_out else _display_peer(m.get("fromAddress") or sel_tid or "")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.get_style_context().add_class("notif-row")
            lbl = Gtk.Label(
                label=f"{sender_tag}: {mbody or ('[MMS Attachment]' if is_mms else '')}",
                xalign=0.0,
            )
            lbl.set_line_wrap(True)
            lbl.set_selectable(True)
            row.pack_start(lbl, True, True, 0)
            if is_mms:
                media_key = str((medias[0].get("path") if medias and isinstance(medias[0], dict) else "") or mid)
                cached_mms = (st.get("cached_mms_files") or {}).get(media_key)
                if cached_mms and Path(cached_mms).is_file():
                    open_att_btn = Gtk.Button(label="View MMS")
                    open_att_btn.get_style_context().add_class("secondary-btn")
                    open_att_btn.connect(
                        "clicked",
                        lambda *_, cf=cached_mms, m_id=mid: self._open_gallery_preview_dialog(
                            {"name": f"MMS_{m_id}.jpg", "path": cf}, cf, cf
                        ),
                    )
                    row.pack_end(open_att_btn, False, False, 0)
                else:
                    att_btn = Gtk.Button(label="Fetch MMS Media")
                    att_btn.get_style_context().add_class("secondary-btn")
                    att_btn.connect("clicked", lambda *_, mk=media_key: self._spawn(self.facade.request(f"messages-mms:{mk}")))
                    row.pack_end(att_btn, False, False, 0)
            thread_box.pack_start(row, False, False, 0)

        conv_box.show_all()
        thread_box.show_all()

MessagesPage = MessagesPageMixin
