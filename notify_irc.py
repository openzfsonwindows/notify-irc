#! /usr/bin/env python

import os
import sys
import argparse
import asyncio
import fnmatch
import logging
import urllib.request

import pydle
import json

from enum import Enum

class ColorMode(Enum):
    NONE = 0
    ANSI = 1
    MIRC = 2

log = logging.getLogger(__name__)


class NotifyIRC(pydle.Client):
    def __init__(self, channel, channel_key, notification, use_notice=False, **kwargs):
        super().__init__(**kwargs)
        self.channel = channel if channel.startswith("#") else f"#{channel}"
        self.channel_key = channel_key
        self.notification = notification
        self.use_notice = use_notice
        self.future = None
        self.delivered = False

    async def on_connect(self):
        await super().on_connect()
        if self.use_notice:
            await self.notice(self.channel, self.notification)
            # Need to issue a command and await the response before we quit,
            # otherwise we are disconnected before the notice is processed
            self.future = self.eventloop.create_future()
            await self.rawmsg("VERSION")
            await self.future
            self.delivered = True
            await self.quit()
        else:
            await self.join(self.channel, self.channel_key)

    async def on_join(self, channel, user):
        await super().on_join(channel, user)
        if user != self.nickname:
            return
        await self.message(self.channel, self.notification)
        self.delivered = True
        await self.part(self.channel)

    async def on_part(self, channel, user, message=None):
        await super().on_part(channel, user, message)
        await self.quit()

    async def on_raw_351(self, message):
        """VERSION response"""
        if self.future:
            self.future.set_result(None)

def parse_event_file(event_path, ansicolor, token=None):
    """Parse the event JSON file and handle different event types."""
    with open(event_path, 'r') as f:
        print("Debug: Successfully opened file.")
        event_data = json.load(f)

        # Determine the type of event
        event_type = os.getenv("GITHUB_EVENT_NAME", "unknown")
        print(f"Debug: Event type is '{event_type}'")
        if event_type == "push":
            return parse_push(event_data, ansicolor, token)
        elif event_type == "issues":
            return parse_issue(event_data, ansicolor)
        elif event_type == "issue_comment":
            return parse_issue_comment(event_data, ansicolor)
        elif event_type == "pull_request":
            return parse_pull(event_data, ansicolor)
        elif event_type == "discussion":
            return parse_discussion(event_data, ansicolor)
        elif event_type == "discussion_comment":
            return parse_discussion_comment(event_data, ansicolor)
        elif event_type == "create":
            return parse_create(event_data, ansicolor)
        elif event_type == "delete":
            return parse_delete(event_data, ansicolor)
        else:
            print("Debug: Unsupported or unknown event type.")
    return "No actionable event found."

ansi_to_mirc = {
    "30": "01",  # ANSI Black -> mIRC Black
    "31": "04",  # ANSI Red -> mIRC Red
    "32": "03",  # ANSI Green -> mIRC Green
    "33": "07",  # ANSI Yellow -> mIRC Yellow
    "34": "02",  # ANSI Blue -> mIRC Blue
    "35": "06",  # ANSI Magenta -> mIRC Magenta
    "36": "10",  # ANSI Cyan -> mIRC Cyan
    "37": "00",  # ANSI White -> mIRC White
    # Bright colors
    "90": "14",  # Bright Black -> Light Grey
    "91": "05",  # Bright Red -> Light Red
    "92": "09",  # Bright Green -> Light Green
    "93": "08",  # Bright Yellow -> Yellow
    "94": "12",  # Bright Blue -> Light Blue
    "95": "13",  # Bright Magenta -> Light Magenta
    "96": "11",  # Bright Cyan -> Light Cyan
    "97": "15",  # Bright White -> White
}

def translate_ansi_to_mirc(ansi_color):
    """Translate ANSI color code to mIRC color code."""
    return ansi_to_mirc.get(ansi_color, "00")  # Default to white if unknown

def colorize(text, color_code, enable_color):

    if enable_color == ColorMode.ANSI:
        return f"\u001b[{color_code}m{text}\u001b[0m"
    elif enable_color == ColorMode.MIRC:
        mirc_color = translate_ansi_to_mirc(color_code)
        return f"\x03{mirc_color}{text}\x03"

    return text

# GitHub only includes the first 20 commits of a push in the event payload.
PUSH_PAYLOAD_LIMIT = 20


def actor(event_data, fallback="unknown"):
    """Who performed the action: the event's sender, not the item's author."""
    return (event_data.get("sender") or {}).get("login") or fallback


def push_commit_count(event_data, token=None):
    """Return (count, exact). The payload caps commits at 20, so ask the
    compare API for the real number when the list may have been truncated."""
    commits = event_data.get("commits", [])
    if len(commits) < PUSH_PAYLOAD_LIMIT:
        return len(commits), True

    repo = (event_data.get("repository") or {}).get("full_name")
    before = event_data.get("before")
    after = event_data.get("after")
    if repo and before and after and set(before) != {"0"}:
        url = f"https://api.github.com/repos/{repo}/compare/{before}...{after}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "notify-irc",
        })
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                total = json.load(resp).get("total_commits")
            if isinstance(total, int) and total >= len(commits):
                return total, True
        except Exception as e:
            log.warning("compare API failed, count is approximate: %s", e)
    return len(commits), False


def parse_push(event_data, ansicolor, token=None):
    """Parse the event JSON file to extract commit messages."""
    commits = []
    commit_messages = []

    # Extract necessary fields from the event data
    repo_name = event_data.get("repository", {}).get("name", "unknown")
    repo_name = colorize(repo_name, "34", ansicolor) 
    pusher = actor(event_data, event_data.get("pusher", {}).get("name", "unknown"))
    pusher = colorize(pusher, "33", ansicolor) 
    branch = event_data.get("ref", "").split("/")[-1]
    branch = colorize(branch, "32", ansicolor) 
    compare_url = event_data.get("compare", "")
    compare_url = colorize(compare_url, "35", ansicolor) 
    commits = event_data.get("commits", [])

    # Commit count and summary line
    # <zfs-consus> [openzfs] lundman pushed 1 commits to main [+0/-0]
    # <zfs-consus> https://github.com/openzfsonwindows/openzfs/compare/commit1...commit2
    # <zfs-consus> [openzfs] lundman 33fecac - Tell Inno to start zed service
        
    if len(commits) == 0:
        return ""

    commit_count, exact = push_commit_count(event_data, token)
    commit_count_str = colorize(f"{commit_count}{'' if exact else '+'}", "32", ansicolor)
    noun = "commit" if commit_count == 1 and exact else "commits"
    commit_messages.append(f"[{repo_name}] {pusher} pushed {commit_count_str} {noun} to {branch} {compare_url}")

    # Detailed commit messages
    # for commit in commits:
    for i, commit in enumerate(commits[:8]):
        short_sha = commit["id"][:7]
        short_sha = colorize(short_sha, "32", ansicolor) 
        message = commit["message"].split("\n", 1)[0]
        commit_messages.append(f"[{repo_name}] {pusher} {short_sha} - {message}")
    if commit_count > 8:
        commit_messages.append(f"[{repo_name}] ... and {commit_count - 8}{'' if exact else '+'} more")
    return "\n".join(commit_messages)

def parse_issue(event_data, ansicolor):
    """Parse the event JSON file to extract issues."""
    repo_name = event_data.get("repository", {}).get("name", "unknown")
    repo_name = colorize(repo_name, "34", ansicolor) 
    issue_action = event_data.get("action", "unknown")
    issue = event_data.get("issue", {})
    issue_number = issue.get("number", "unknown")
    issue_number_str = colorize(str(issue_number), "32", ansicolor) 
    issue_title = issue.get("title", "No title")
    issue_url = issue.get("html_url", "No URL")
    issue_url = colorize(issue_url, "35", ansicolor) 
    creator = actor(event_data, (issue.get("user") or {}).get("login", "unknown"))
    creator = colorize(creator, "33", ansicolor) 
    commit_messages = []

    if issue_action == "opened":
            verb = "created"
    elif issue_action == "edited":
            verb = "edited"
    elif issue_action == "closed":
            verb = "closed"
    else:
            verb = "verbed"
    
    commit_messages.append(f"[{repo_name}] {creator} {verb} issue #{issue_number_str}: {issue_title} - {issue_url}")
    return "\n".join(commit_messages)

def parse_issue_comment(event_data, ansicolor):
    """Parse the event JSON file to extract issue comments."""
    repo_name = event_data.get("repository", {}).get("name", "unknown")
    repo_name = colorize(repo_name, "34", ansicolor) 
    issue_action = event_data.get("action", "unknown")

    commenter = actor(event_data, event_data["comment"]["user"]["login"])
    commenter = colorize(commenter, "33", ansicolor) 
    issue_number = event_data["issue"]["number"]
    issue_number_str = colorize(str(issue_number), "32", ansicolor) 
    issue_title = event_data["issue"]["title"]
    # comment_body = event_data["comment"]["body"]
    issue_url = event_data["comment"]["html_url"]
    issue_url = colorize(issue_url, "35", ansicolor) 
    commit_messages = []

    commit_messages.append(f"[{repo_name}] {commenter} {issue_action} a comment on issue #{issue_number_str}: {issue_title} - {issue_url}")
    return "\n".join(commit_messages)

def parse_pull(event_data, ansicolor):
    """Parse the event JSON file to extract pull requests."""
    commits = []
    commit_messages = []

    # Extract necessary fields from the event data
    repo_name = event_data.get("repository", {}).get("name", "unknown")
    repo_name = colorize(repo_name, "34", ansicolor) 
    action = event_data.get("action", "unknown")
    user = actor(event_data, event_data["pull_request"]["user"]["login"])
    user = colorize(user, "33", ansicolor) 
    title = event_data["pull_request"]["title"]
    base_branch = event_data["pull_request"]["base"]["ref"]
    base_branch = colorize(base_branch, "32", ansicolor) 
    pr_number = event_data["pull_request"]["number"]
    pr_number_str = colorize(str(pr_number), "32", ansicolor) 
    compare_url = event_data["pull_request"].get("html_url", "")
    compare_url = colorize(compare_url, "35", ansicolor) 
    commits = event_data.get("commits", [])

    if action == "opened":
        verb = "created PR"
    elif action == "closed":
        if event_data["pull_request"]["merged"]:
            verb = "merged PR"
        else:
            verb = "closed PR"
    elif action == "synchronize":
        verb = f"pushed new commits to PR"
    elif action == "edited":
        verb = "edited PR"
    else:
        verb = f"performed '{action}' on PR"
    
    commit_messages.append(f"[{repo_name}] {user} {verb} #{pr_number} [{base_branch}]: {title} {compare_url}")
    
    # It seems synchronize does not include list of commits, we need
    # to fetch them by API. TODO
    for i, commit in enumerate(commits[:8]):
        short_sha = commit["id"][:7]
        short_sha = colorize(short_sha, "32", ansicolor) 
        message = commit["message"]
        commit_messages.append(f"[{repo_name}] {user} {short_sha} - {message}")
    return "\n".join(commit_messages)

def parse_discussion(event_data, ansicolor):
    """Parse the event JSON file to extract discussions."""
    repo_name = event_data.get("repository", {}).get("name", "unknown")
    repo_name = colorize(repo_name, "34", ansicolor) 
    discussion_action = event_data.get("action", "unknown")
    discussion = event_data.get("discussion", {})
    discussion_number = discussion.get("number", "unknown")
    discussion_number_str = colorize(str(discussion_number), "32", ansicolor) 
    discussion_title = discussion.get("title", "No title")
    discussion_url = discussion.get("html_url", "No URL")
    discussion_url = colorize(discussion_url, "35", ansicolor) 
    creator = event_data["sender"].get("login", "unknown")
    creator = colorize(creator, "33", ansicolor) 
    commit_messages = []

    if discussion_action == "created":
            verb = "created"
    elif discussion_action == "edited":
            verb = "edited"
    elif discussion_action == "closed":
            verb = "closed"
    elif discussion_action == "answered":
            verb = "answered"
    else:
            verb = "verbed"
    
    commit_messages.append(f"[{repo_name}] {creator} {verb} discussion #{discussion_number_str}: {discussion_title} - {discussion_url}")
    return "\n".join(commit_messages)

def parse_discussion_comment(event_data, ansicolor):
    """Parse the event JSON file to extract discussion comments."""
    repo_name = event_data.get("repository", {}).get("name", "unknown")
    repo_name = colorize(repo_name, "34", ansicolor) 
    discussion_action = event_data.get("action", "unknown")

    commenter = actor(event_data, event_data["comment"]["user"]["login"])
    commenter = colorize(commenter, "33", ansicolor) 
    discussion_number = event_data["discussion"]["number"]
    discussion_number_str = colorize(str(discussion_number), "32", ansicolor) 
    discussion_title = event_data["discussion"]["title"]
    # comment_body = event_data["comment"]["body"]
    discussion_url = event_data["comment"]["html_url"]
    discussion_url = colorize(discussion_url, "35", ansicolor) 
    commit_messages = []

    commit_messages.append(f"[{repo_name}] {commenter} {discussion_action} a comment on discussion #{discussion_number_str}: {discussion_title} - {discussion_url}")
    return "\n".join(commit_messages)

def parse_create(event_data, ansicolor):
    repo_name = event_data.get("repository", {}).get("name", "unknown")
    repo_name = colorize(repo_name, "34", ansicolor) 
    user = event_data["sender"]["login"]  
    user = colorize(user, "33", ansicolor) 
    commit_messages = []

    ref = event_data["ref"]
    ref = colorize(ref, "32", ansicolor) 
    ref_type = event_data["ref_type"]

    commit_messages.append(f"[{repo_name}] {user} created new {ref_type}: {ref}")
    return "\n".join(commit_messages)

def parse_delete(event_data, ansicolor):
    repo_name = event_data.get("repository", {}).get("name", "unknown")
    repo_name = colorize(repo_name, "34", ansicolor) 
    user = event_data["sender"]["login"]  
    user = colorize(user, "33", ansicolor) 
    commit_messages = []

    ref = event_data["ref"]
    ref = colorize(ref, "32", ansicolor) 
    ref_type = event_data["ref_type"]

    commit_messages.append(f"[{repo_name}] {user} delete {ref_type}: {ref}")
    return "\n".join(commit_messages)

def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="irc.libera.chat",
                        help="IRC server, or a comma separated list of host[:port] to try in order")
    parser.add_argument("-p", "--port", default=6667, type=int)
    parser.add_argument("--password", default=None, help="Optional server password")
    parser.add_argument("--nickname", default="github-notify")
    parser.add_argument(
        "--sasl-password", help="Nickname password for SASL authentication"
    )
    parser.add_argument("--channel", required=True, help="IRC #channel")
    parser.add_argument("--channel-key", help="IRC #channel password")
    parser.add_argument("--tls", action="store_true")
    parser.add_argument(
        "--notice", action="store_true", help="Use NOTICE instead of PRIVMSG"
    )
    parser.add_argument("--timeout", default=15, type=float,
                        help="Seconds allowed per server to connect and deliver")
    parser.add_argument("--only-repository", default=None,
                        help="Comma separated owner/repo patterns; do nothing on other repositories")
    parser.add_argument("--github-token", default=None,
                        help="Token for the API, used to count large pushes")
    parser.add_argument("--eventpath", required=True, help="Path to the GitHub event file")
    parser.add_argument("--ansicolor", action="store_true", help="Enable ANSI color text")
    parser.add_argument("--mirccolor", action="store_true", help="Enable MIRC color text")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def repository_allowed(patterns, repository):
    """True if no restriction is set or the repository matches one."""
    patterns = [p.strip() for p in (patterns or "").split(",") if p.strip()]
    if not patterns:
        return True
    return any(fnmatch.fnmatch((repository or "").lower(), p.lower()) for p in patterns)


def split_server(entry, default_port):
    """'host' or 'host:port' -> (host, port)."""
    host, sep, port = entry.rpartition(":")
    if sep and port.isdigit() and host and ":" not in host:
        return host, int(port)
    return entry, default_port


def deliver(args, server, port, notification):
    """Try one server. Returns True once the message has been sent."""
    client = NotifyIRC(
        channel=args.channel,
        channel_key=args.channel_key or None,
        notification=notification,
        use_notice=args.notice,
        nickname=args.nickname,
        sasl_username=args.nickname,
        sasl_password=args.sasl_password or None,
    )
    loop = client.eventloop
    try:
        loop.run_until_complete(asyncio.wait_for(
            client.connect(
                hostname=server,
                port=port,
                password=args.password or None,
                tls=args.tls,
                # https://github.com/Shizmob/pydle/pull/84
                # tls_verify=args.tls,
            ), args.timeout))
    except asyncio.TimeoutError:
        print(f"{server}: no connection within {args.timeout:g}s", file=sys.stderr)
        return False
    except Exception as e:
        print(f"{server}: connect failed: {type(e).__name__}: {e}", file=sys.stderr)
        return False

    # Connected: let the client join, announce and quit (it stops the loop),
    # but do not wait forever on a server that never finishes registering.
    watchdog = loop.call_later(args.timeout, loop.stop)
    try:
        loop.run_forever()
    finally:
        watchdog.cancel()
        try:
            if client.connected:
                loop.run_until_complete(client.disconnect(expected=True))
        except Exception:
            pass
    if not client.delivered:
        print(f"{server}: connected but the message was not delivered", file=sys.stderr)
    return client.delivered


def main():
    args = get_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    repository = os.getenv("GITHUB_REPOSITORY", "")
    if not repository_allowed(args.only_repository, repository):
        print(f"Skipping: '{repository}' is not in only_repository "
              f"'{args.only_repository}'")
        return 0

    enable_color = ColorMode.NONE
    if args.ansicolor:
        enable_color = ColorMode.ANSI
    elif args.mirccolor:
        enable_color = ColorMode.MIRC

    print(f"ansicolor is '{enable_color}'")

    # Parse the event file to get commit messages
    token = args.github_token or os.getenv("GITHUB_TOKEN") or None
    notification_message = parse_event_file(args.eventpath, enable_color, token)
    if not notification_message:
        return 0

    servers = [x.strip() for x in args.server.split(",") if x.strip()]
    for entry in servers:
        server, port = split_server(entry, args.port)
        if deliver(args, server, port, notification_message):
            return 0
    print("Could not deliver the notification to any server", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
