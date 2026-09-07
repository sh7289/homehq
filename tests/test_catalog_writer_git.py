class _FakeResult:
    returncode = 0


def test_git_commit_and_push_runs_add_commit_push_in_order(tmp_path):
    from catalog_writer import git_commit_and_push

    calls = []

    git_commit_and_push(
        str(tmp_path),
        "Add Kind of Blue",
        github_token="fake-token",
        runner=_runner_with_origin(calls, "https://github.com/sh7289/homehq.git"),
    )

    commands = [c[0] for c in calls]
    # Stages only the catalog: `git add -A` here would publish anything
    # untracked sitting in the app directory.
    assert commands[0] == ["git", "add", "--", "content", "photos"]
    assert commands[1][:3] == ["git", "commit", "-m"]
    assert any("push" in c for c in commands)
    # token never appears in any argv
    assert not any("fake-token" in " ".join(c) for c in commands)


def test_git_commit_and_push_sets_askpass_env_for_push_only(tmp_path):
    from catalog_writer import git_commit_and_push

    calls = []

    git_commit_and_push(
        str(tmp_path),
        "Add item",
        github_token="fake-token",
        runner=_runner_with_origin(calls, "https://github.com/sh7289/homehq.git"),
    )

    push_call = [c for c in calls if "push" in c[0]][0]
    assert "GIT_ASKPASS" in push_call[1]["env"]
    # add/commit must not carry the token env
    assert "env" not in calls[0][1]


class _FakeUrlResult:
    """A runner result that also carries stdout, for `git remote get-url`."""

    returncode = 0

    def __init__(self, stdout=""):
        self.stdout = stdout


def _runner_with_origin(calls, origin_url):
    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[:3] == ["git", "remote", "get-url"]:
            return _FakeUrlResult(origin_url + "\n")
        return _FakeUrlResult()

    return fake_runner


def test_push_url_converts_ssh_origin_to_https():
    from catalog_writer import push_url_for

    assert (
        push_url_for("git@github.com:sh7289/homehq.git")
        == "https://github.com/sh7289/homehq.git"
    )
    assert (
        push_url_for("ssh://git@github.com/sh7289/homehq.git")
        == "https://github.com/sh7289/homehq.git"
    )


def test_push_url_leaves_https_origin_alone():
    from catalog_writer import push_url_for

    assert (
        push_url_for("https://github.com/sh7289/homehq.git")
        == "https://github.com/sh7289/homehq.git"
    )


def test_push_targets_https_url_not_the_ssh_remote(tmp_path):
    """The server's origin is SSH with a read-only deploy key, so GIT_ASKPASS
    is ignored and the token can never authenticate. Push to HTTPS instead."""
    from catalog_writer import git_commit_and_push

    calls = []
    git_commit_and_push(
        str(tmp_path),
        "Add item",
        github_token="fake-token",
        runner=_runner_with_origin(calls, "git@github.com:sh7289/homehq.git"),
    )

    push_cmd = [c[0] for c in calls if "push" in c[0]][0]
    assert "https://github.com/sh7289/homehq.git" in push_cmd
    assert "origin" not in push_cmd
    assert not any("fake-token" in " ".join(c[0]) for c in calls)


def test_push_failure_still_leaves_the_commit_in_place(tmp_path):
    """A failed push must not look like a failed commit -- the catalog file is
    already written and committed locally."""
    import subprocess

    from catalog_writer import PushFailed, git_commit_and_push

    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["git", "remote", "get-url"]:
            return _FakeUrlResult("git@github.com:sh7289/homehq.git\n")
        if "push" in cmd:
            raise subprocess.CalledProcessError(1, cmd, stderr="read only")
        return _FakeUrlResult()

    try:
        git_commit_and_push(
            str(tmp_path), "Add item", github_token="t", runner=fake_runner
        )
        raise AssertionError("expected PushFailed")
    except PushFailed:
        pass

    assert ["git", "commit", "-m", "Add item"] in calls


def test_push_failure_carries_gits_own_error(tmp_path):
    """A generic 'push failed' tells you nothing. Surface git's stderr."""
    import subprocess

    from catalog_writer import PushFailed, git_commit_and_push

    def fake_runner(cmd, **kwargs):
        if cmd[:3] == ["git", "remote", "get-url"]:
            return _FakeUrlResult("git@github.com:sh7289/homehq.git\n")
        if "push" in cmd:
            raise subprocess.CalledProcessError(
                128, cmd, stderr="remote: Write access to repository not granted."
            )
        return _FakeUrlResult()

    try:
        git_commit_and_push(str(tmp_path), "Add item", github_token="t", runner=fake_runner)
        raise AssertionError("expected PushFailed")
    except PushFailed as exc:
        assert "Write access to repository not granted" in str(exc)


def test_nothing_to_commit_is_not_treated_as_a_failure(tmp_path):
    """git exits non-zero when there is nothing staged; that is a no-op here,
    not an error worth 500ing the approve over."""
    import subprocess

    from catalog_writer import git_commit_and_push

    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["git", "remote", "get-url"]:
            return _FakeUrlResult("https://github.com/sh7289/homehq.git\n")
        if cmd[:2] == ["git", "commit"]:
            raise subprocess.CalledProcessError(
                # CalledProcessError takes output=, not stdout= (which is an alias)
                1, cmd, output="nothing to commit, working tree clean"
            )
        return _FakeUrlResult()

    git_commit_and_push(str(tmp_path), "Add item", github_token="t", runner=fake_runner)

    assert not any("push" in c for c in calls), "nothing committed, so nothing to push"
