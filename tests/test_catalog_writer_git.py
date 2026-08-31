class _FakeResult:
    returncode = 0


def test_git_commit_and_push_runs_add_commit_push_in_order(tmp_path):
    from catalog_writer import git_commit_and_push

    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _FakeResult()

    git_commit_and_push(
        str(tmp_path), "Add Kind of Blue", github_token="fake-token", runner=fake_runner
    )

    commands = [c[0] for c in calls]
    assert commands[0] == ["git", "add", "-A"]
    assert commands[1][:3] == ["git", "commit", "-m"]
    assert "push" in commands[2]
    # token never appears in any argv
    assert not any("fake-token" in " ".join(c) for c in commands)


def test_git_commit_and_push_sets_askpass_env_for_push_only(tmp_path):
    from catalog_writer import git_commit_and_push

    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _FakeResult()

    git_commit_and_push(
        str(tmp_path), "Add item", github_token="fake-token", runner=fake_runner
    )

    push_call = calls[2]
    assert "GIT_ASKPASS" in push_call[1]["env"]
