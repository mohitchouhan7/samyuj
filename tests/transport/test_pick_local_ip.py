from __future__ import annotations

import unittest

from samyuj.transport.network import pick_local_ip_for_peer, pick_startup_ip

class PickLocalIpForPeerTest(unittest.TestCase):

    def test_same_24_wins_over_default_route(self) -> None:
        cands = ["172.16.0.2", "192.168.31.2"]
        self.assertEqual(
            pick_local_ip_for_peer("192.168.31.184", cands, "172.16.0.2"),
            "192.168.31.2")

    def test_wifi_preferred_on_subnet_tie(self) -> None:
        cands = ["192.168.31.50", "192.168.31.2"]
        self.assertEqual(
            pick_local_ip_for_peer("192.168.31.184", cands, "172.16.0.2",
                                   preferred=["192.168.31.2"]),
            "192.168.31.2")

    def test_same_16_is_second_best(self) -> None:
        cands = ["10.0.0.5", "192.168.0.9"]
        self.assertEqual(
            pick_local_ip_for_peer("192.168.31.184", cands, "10.0.0.5"),
            "192.168.0.9")

    def test_no_match_keeps_current(self) -> None:
        cands = ["172.16.0.2", "10.1.2.3"]
        self.assertEqual(
            pick_local_ip_for_peer("192.168.31.184", cands, "172.16.0.2"),
            "172.16.0.2")

    def test_never_returns_the_peer_address(self) -> None:
        self.assertEqual(
            pick_local_ip_for_peer("192.168.31.2", ["192.168.31.2"], "10.0.0.1"),
            "10.0.0.1")

    def test_empty_or_loopback_peer_keeps_current(self) -> None:
        self.assertEqual(pick_local_ip_for_peer("", ["10.0.0.1"], "10.0.0.9"), "10.0.0.9")
        self.assertEqual(pick_local_ip_for_peer("127.0.0.1", ["10.0.0.1"], "10.0.0.9"), "10.0.0.9")

    def test_empty_candidates_keeps_current(self) -> None:
        self.assertEqual(pick_local_ip_for_peer("192.168.31.184", [], "10.0.0.9"), "10.0.0.9")

class PickStartupIpTest(unittest.TestCase):

    def test_wifi_ip_beats_vpn_default_route(self) -> None:
        self.assertEqual(
            pick_startup_ip("172.16.0.2",
                            ["172.16.0.2", "192.168.31.2"],
                            ["192.168.31.2"]),
            "192.168.31.2")

    def test_default_route_used_when_it_is_the_wifi(self) -> None:
        self.assertEqual(
            pick_startup_ip("192.168.31.2",
                            ["172.16.0.2", "192.168.31.2"],
                            ["192.168.31.2"]),
            "192.168.31.2")

    def test_no_wifi_falls_back_to_default_route(self) -> None:
        self.assertEqual(
            pick_startup_ip("10.1.2.3", ["10.1.2.3", "192.168.9.9"], []),
            "10.1.2.3")

    def test_nothing_available(self) -> None:
        self.assertEqual(pick_startup_ip("127.0.0.1", [], []), "127.0.0.1")

if __name__ == "__main__":
    unittest.main()
