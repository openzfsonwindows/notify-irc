import asyncio
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import notify_irc as n  # noqa: E402
from fake_irc import Blackhole, FakeIRC  # noqa: E402

NONE = n.ColorMode.NONE


def commit(i):
    return {"id": f"{i:040x}", "message": f"commit {i}\n\nbody"}


class Actor(unittest.TestCase):
    def test_issue_closed_names_the_closer_not_the_author(self):
        ev = {"action": "closed", "repository": {"name": "r"},
              "sender": {"login": "maintainer"},
              "issue": {"number": 5, "title": "t", "html_url": "u",
                        "user": {"login": "reporter"}}}
        msg = n.parse_issue(ev, NONE)
        self.assertIn("maintainer closed issue #5", msg)
        self.assertNotIn("reporter", msg)

    def test_issue_without_sender_falls_back_to_author(self):
        ev = {"action": "opened", "repository": {"name": "r"},
              "issue": {"number": 5, "title": "t", "html_url": "u",
                        "user": {"login": "reporter"}}}
        self.assertIn("reporter created issue", n.parse_issue(ev, NONE))

    def _pr(self, action, merged=False):
        return {"action": action, "repository": {"name": "r"},
                "sender": {"login": "maintainer"},
                "pull_request": {"user": {"login": "author"}, "title": "t",
                                 "base": {"ref": "main"}, "number": 9,
                                 "merged": merged,
                                 "html_url": "https://x/pull/9"}}

    def test_pr_merged_names_the_merger_and_links_the_pr(self):
        msg = n.parse_pull(self._pr("closed", merged=True), NONE)
        self.assertIn("maintainer merged PR #9", msg)
        self.assertIn("https://x/pull/9", msg)
        self.assertNotIn("author", msg)

    def test_pr_closed_unmerged(self):
        self.assertIn("maintainer closed PR", n.parse_pull(self._pr("closed"), NONE))

    def test_pr_with_commits_no_longer_raises(self):
        ev = self._pr("opened")
        ev["commits"] = [commit(1)]
        self.assertIn("maintainer 0000000", n.parse_pull(ev, NONE))

    def test_comment_edited_names_the_editor(self):
        ev = {"action": "edited", "repository": {"name": "r"},
              "sender": {"login": "maintainer"},
              "comment": {"user": {"login": "author"}, "html_url": "u"},
              "issue": {"number": 1, "title": "t"}}
        self.assertIn("maintainer edited a comment", n.parse_issue_comment(ev, NONE))


class Push(unittest.TestCase):
    def _push(self, count, **extra):
        ev = {"repository": {"name": "r", "full_name": "o/r"},
              "pusher": {"name": "pushername"}, "sender": {"login": "pusher"},
              "ref": "refs/heads/main", "compare": "https://x/compare",
              "before": "a" * 40, "after": "b" * 40,
              "commits": [commit(i) for i in range(count)]}
        ev.update(extra)
        return ev

    def test_small_push_is_exact_and_singular(self):
        one = n.parse_push(self._push(1), NONE)
        self.assertIn("pushed 1 commit to main", one)
        three = n.parse_push(self._push(3), NONE)
        self.assertIn("pushed 3 commits to main", three)

    def test_truncated_push_uses_compare_api_count(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = json.dumps({"total_commits": 137}).encode()
        with mock.patch("urllib.request.urlopen", return_value=resp) as u:
            msg = n.parse_push(self._push(20), NONE, token="tok")
        self.assertIn("pushed 137 commits", msg)
        self.assertIn("... and 129 more", msg)
        req = u.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/compare/" + "a" * 40 + "..." + "b" * 40))
        self.assertEqual(req.get_header("Authorization"), "Bearer tok")

    def test_truncated_push_without_api_says_20_plus(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            msg = n.parse_push(self._push(20), NONE)
        self.assertIn("pushed 20+ commits", msg)
        self.assertIn("... and 12+ more", msg)

    def test_new_branch_push_does_not_call_compare(self):
        with mock.patch("urllib.request.urlopen") as u:
            msg = n.parse_push(self._push(20, before="0" * 40), NONE)
        u.assert_not_called()
        self.assertIn("20+", msg)

    def test_empty_push_is_silent(self):
        self.assertEqual(n.parse_push(self._push(0), NONE), "")


class Guard(unittest.TestCase):
    def test_repository_allowed(self):
        f = n.repository_allowed
        self.assertTrue(f(None, "any/thing"))
        self.assertTrue(f("", "any/thing"))
        self.assertTrue(f("openzfsonosx/openzfs", "openzfsonosx/openzfs"))
        self.assertTrue(f("openzfsonosx/*", "OpenZFSonOSX/openzfs-fork"))
        self.assertTrue(f("a/b, c/d", "c/d"))
        self.assertFalse(f("openzfsonosx/*", "someone/openzfs"))

    def test_split_server(self):
        self.assertEqual(n.split_server("irc.x", 6697), ("irc.x", 6697))
        self.assertEqual(n.split_server("irc.x:7000", 6697), ("irc.x", 7000))


class Delivery(unittest.TestCase):
    """Run the real client against local fake servers."""

    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.good, cls.bad = FakeIRC(), Blackhole()

        def run():
            asyncio.set_event_loop(cls.loop)
            cls.loop.run_until_complete(cls.good.start())
            cls.loop.run_until_complete(cls.bad.start())
            cls.ready.set()
            cls.loop.run_forever()

        cls.ready = threading.Event()
        cls.thread = threading.Thread(target=run, daemon=True)
        cls.thread.start()
        assert cls.ready.wait(5)

    @classmethod
    def tearDownClass(cls):
        cls.loop.call_soon_threadsafe(cls.loop.stop)

    def setUp(self):
        self.good.messages.clear()

    def _run(self, servers, event=None, timeout=2, notice=False, repo="o/r", only=None, tls=False):
        ev = event or {"ref": "refs/heads/main", "repository": {"name": "r"},
                       "sender": {"login": "me"}, "commits": [commit(1)],
                       "compare": "c"}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(ev, f)
        argv = ["notify_irc.py", "--server", servers, "--channel", "#c",
                "--nickname", "bot", "--eventpath", f.name,
                "--timeout", str(timeout)]
        if notice:
            argv.append("--notice")
        if tls:
            argv.append("--tls")
        if only:
            argv += ["--only-repository", only]
        env = {"GITHUB_EVENT_NAME": "push", "GITHUB_REPOSITORY": repo}
        with mock.patch.object(sys, "argv", argv), mock.patch.dict(os.environ, env):
            rc = n.main()
        os.unlink(f.name)
        return rc

    def test_delivers_to_a_working_server(self):
        self.assertEqual(self._run(f"127.0.0.1:{self.good.port}"), 0)
        self.assertEqual(len(self.good.messages), 2)
        self.assertIn("pushed 1 commit to main", self.good.messages[0])

    def test_fails_over_from_a_stalled_server(self):
        before = self.bad.connections
        rc = self._run(f"127.0.0.1:{self.bad.port},127.0.0.1:{self.good.port}",
                       timeout=1)
        self.assertEqual(rc, 0)
        self.assertGreater(self.bad.connections, before)
        self.assertEqual(len(self.good.messages), 2)

    def test_stalled_tls_handshake_times_out_quickly(self):
        import time
        start = time.monotonic()
        rc = self._run(f"127.0.0.1:{self.bad.port}", timeout=1, tls=True)
        self.assertEqual(rc, 1)
        self.assertLess(time.monotonic() - start, 5)  # not asyncio's 60s

    def test_reports_failure_when_every_server_fails(self):
        rc = self._run(f"127.0.0.1:{self.bad.port},127.0.0.1:1", timeout=1)
        self.assertEqual(rc, 1)

    def test_only_repository_skips_forks(self):
        rc = self._run(f"127.0.0.1:{self.good.port}", repo="fork/openzfs",
                       only="openzfsonosx/*")
        self.assertEqual(rc, 0)
        self.assertEqual(self.good.messages, [])


if __name__ == "__main__":
    unittest.main()
