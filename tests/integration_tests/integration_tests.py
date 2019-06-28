import os
import subprocess
import pathlib

import pytest
import gitlab

from repobee import util
from repobee import apimeta
from repobee import gitlab_api

assert os.getenv(
    "REPOBEE_NO_VERIFY_SSL"
), "The env variable REPOBEE_NO_VERIFY_SSL must be set to 'true'"


VOLUME_DST = "/workdir"
DIR = pathlib.Path(__file__).resolve().parent
TOKEN = (DIR / "token").read_text(encoding="utf-8").strip()
RESTORE_SH = DIR / "restore.sh"

OAUTH_USER = "oauth2"
BASE_DOMAIN = "gitlab.integrationtest.local"
BASE_URL = "https://" + BASE_DOMAIN
LOCAL_DOMAIN = "localhost:50443"
LOCAL_BASE_URL = "https://" + LOCAL_DOMAIN
ORG_NAME = "repobee-testing"
MASTER_ORG_NAME = "repobee-master"
ACTUAL_USER = "repobee-user"

MASTER_REPO_NAMES = "task-1 task-2 task-3".split()
STUDENT_TEAMS = [apimeta.Team(members=[s]) for s in "slarse rjglasse".split()]
STUDENT_TEAM_NAMES = [str(t) for t in STUDENT_TEAMS]


def api_instance(org_name=ORG_NAME):
    """Return a valid instance of the GitLabAPI class."""
    return gitlab_api.GitLabAPI(LOCAL_BASE_URL, TOKEN, org_name)


def gitlab_and_groups():
    """Return a valid gitlab instance, along with the master group and the
    target group.
    """
    gl = gitlab.Gitlab(LOCAL_BASE_URL, private_token=TOKEN, ssl_verify=False)
    master_group = gl.groups.list(search=MASTER_ORG_NAME)[0]
    target_group = gl.groups.list(search=ORG_NAME)[0]
    return gl, master_group, target_group


def assert_master_repos_exist(master_repo_names, org_name):
    """Assert that the master repos are in the specified group."""
    gl, *_ = gitlab_and_groups()
    group = gl.groups.list(search=org_name)[0]
    actual_repo_names = [g.name for g in group.projects.list()]
    assert sorted(actual_repo_names) == sorted(master_repo_names)


def assert_repos_exist(student_teams, master_repo_names, org_name=ORG_NAME):
    """Assert that the associated student repos exist."""
    repo_names = util.generate_repo_names(student_teams, master_repo_names)
    gl = gitlab.Gitlab(LOCAL_BASE_URL, private_token=TOKEN, ssl_verify=False)
    target_group = gl.groups.list(search=ORG_NAME)[0]
    student_groups = gl.groups.list(id=target_group.id)

    projects = [p for g in student_groups for p in g.projects.list()]
    project_names = [p.name for p in projects]

    assert set(project_names) == set(repo_names)


def assert_groups_exist(student_teams, org_name=ORG_NAME):
    """Assert that the expected student groups exist and that the expected
    members are in them.
    """
    gl = gitlab.Gitlab(LOCAL_BASE_URL, private_token=TOKEN, ssl_verify=False)
    target_group = gl.groups.list(search=ORG_NAME)[0]
    sorted_teams = sorted(list(student_teams), key=lambda t: t.name)

    sorted_groups = sorted(
        list(gl.groups.list(id=target_group.id)), key=lambda g: g.name
    )
    assert set(g.name for g in sorted_groups) == set(
        str(team) for team in sorted_teams
    )
    for group, team in zip(sorted_groups, sorted_teams):
        # the user who owns the OAUTH token is always listed as a member
        # of groups he/she creates
        expected_members = sorted(team.members + [ACTUAL_USER])
        actual_members = sorted(m.username for m in group.members.list())

        assert group.name == team.name
        assert actual_members == expected_members


def _assert_on_projects(student_teams, master_repo_names, assertion):
    """Execute the specified assertion operation on a project. Assertion should
    be a callable taking precisely on project as an argument.
    """
    gl = gitlab.Gitlab(LOCAL_BASE_URL, private_token=TOKEN, ssl_verify=False)
    target_group = gl.groups.list(search=ORG_NAME)[0]
    student_groups = gl.groups.list(id=target_group.id)
    projects = [
        gl.projects.get(p.id, lazy=True)
        for g in student_groups
        for p in g.projects.list()
    ]

    for proj in projects:
        assertion(proj)


def assert_issues_exist(
    student_teams, master_repo_names, expected_issue, expected_state="opened"
):
    """Assert that the expected issue has been opened in each of the student
    repos.
    """

    def assertion(project):
        issues = project.issues.list()
        for actual_issue in issues:
            if actual_issue.title == expected_issue.title:
                assert actual_issue.state == expected_state
                return
        assert False, "no issue matching the specified title"

    _assert_on_projects(student_teams, master_repo_names, assertion)


def assert_num_issues(student_teams, master_repo_names, num_issues):
    """Assert that there are precisely num_issues issues in each student
    repo.
    """

    def assertion(project):
        assert len(project.issues.list()) == num_issues

    _assert_on_projects(student_teams, master_repo_names, assertion)


@pytest.fixture(autouse=True)
def restore():
    """Run the script that restores the GitLab instance to its initial
    state.
    """
    with open(os.devnull, "w") as devnull:
        subprocess.run(
            str(RESTORE_SH), shell=True, stdout=devnull, stderr=devnull
        )


@pytest.fixture
def tmpdir_volume_arg(tmpdir):
    """Create a temporary directory and return an argument string that
    will mount a docker volume to it.
    """
    yield "-v {}:{}".format(str(tmpdir), VOLUME_DST)


@pytest.fixture
def with_student_repos(restore):
    """Set up student repos before starting tests.

    Note that explicitly including restore here is necessary to ensure that
    it runs before this fixture.
    """
    command = (
        "repobee setup -u {} -g {} -o {} -mo {} -mn {} -s {} -t {} -tb"
    ).format(
        OAUTH_USER,
        BASE_URL,
        ORG_NAME,
        MASTER_ORG_NAME,
        " ".join(MASTER_REPO_NAMES),
        " ".join(STUDENT_TEAM_NAMES),
        TOKEN,
    )

    run_in_docker(command)

    # pre-test asserts
    assert_repos_exist(STUDENT_TEAMS, MASTER_REPO_NAMES)
    assert_groups_exist(STUDENT_TEAMS)


@pytest.fixture
def open_issues(with_student_repos):
    """Open two issues in each student repo."""
    task_issue = apimeta.Issue(
        title="Task", body="The task is to do this, this and that"
    )
    correction_issue = apimeta.Issue(
        title="Correction required", body="You need to fix this, this and that"
    )
    issues = [task_issue, correction_issue]
    gl = gitlab.Gitlab(LOCAL_BASE_URL, private_token=TOKEN, ssl_verify=False)
    target_group = gl.groups.list(search=ORG_NAME)[0]
    projects = (
        gl.projects.get(p.id)
        for p in target_group.projects.list(include_subgroups=True)
    )
    for project in projects:
        project.issues.create(
            dict(title=task_issue.title, body=task_issue.body)
        )
        project.issues.create(
            dict(title=correction_issue.title, body=correction_issue.body)
        )

    assert_num_issues(STUDENT_TEAM_NAMES, MASTER_REPO_NAMES, len(issues))
    assert_issues_exist(STUDENT_TEAM_NAMES, MASTER_REPO_NAMES, task_issue)
    assert_issues_exist(
        STUDENT_TEAM_NAMES, MASTER_REPO_NAMES, correction_issue
    )

    return issues


def run_in_docker(command, extra_args=""):
    docker_command = (
        "docker run {} --net development --rm --name repobee "
        "repobee:test /bin/sh -c '{}'"
    ).format(extra_args, command)
    return subprocess.run(docker_command, shell=True)


@pytest.mark.filterwarnings("ignore:.*Unverified HTTPS request.*")
class TestSetup:
    """Integration tests for the setup command."""

    def test_clean_setup(self):
        """Test a first-time setup with master repos in the master org."""
        command = (
            "repobee setup -u {} -g {} -o {} -mo {} -mn {} -s {} -t {} -tb"
        ).format(
            OAUTH_USER,
            BASE_URL,
            ORG_NAME,
            MASTER_ORG_NAME,
            " ".join(MASTER_REPO_NAMES),
            " ".join(STUDENT_TEAM_NAMES),
            TOKEN,
        )

        result = run_in_docker(command)
        assert result.returncode == 0
        assert_repos_exist(STUDENT_TEAMS, MASTER_REPO_NAMES)
        assert_groups_exist(STUDENT_TEAMS)

    def test_setup_twice(self):
        """Setting up twice should have the same effect as setting up once."""
        command = (
            "repobee setup -u {} -g {} -o {} -mo {} -mn {} -s {} -t {} -tb"
        ).format(
            OAUTH_USER,
            BASE_URL,
            ORG_NAME,
            MASTER_ORG_NAME,
            " ".join(MASTER_REPO_NAMES),
            " ".join(STUDENT_TEAM_NAMES),
            TOKEN,
        )

        result = run_in_docker(command)
        result = run_in_docker(command)
        assert result.returncode == 0
        assert_repos_exist(STUDENT_TEAMS, MASTER_REPO_NAMES)
        assert_groups_exist(STUDENT_TEAMS)


@pytest.mark.filterwarnings("ignore:.*Unverified HTTPS request.*")
class TestMigrate:
    """Integration tests for the migrate command."""

    def test_happy_path(self):
        """Migrate a few repos from the existing master repo into the target
        organization.
        """
        api = api_instance(MASTER_ORG_NAME)
        master_repo_urls = [
            url.replace(LOCAL_DOMAIN, BASE_DOMAIN)
            for url in api.get_repo_urls(MASTER_REPO_NAMES)
        ]
        # clone the master repos to disk first first
        git_commands = ["git clone {}".format(url) for url in master_repo_urls]
        repobee_command = (
            "repobee migrate -u {} -g {} -o {} -mn {} -t {} -tb"
        ).format(
            OAUTH_USER, BASE_URL, ORG_NAME, " ".join(MASTER_REPO_NAMES), TOKEN
        )
        command = " && ".join(git_commands + [repobee_command])

        result = run_in_docker(command)

        assert result.returncode == 0
        assert_master_repos_exist(MASTER_REPO_NAMES, ORG_NAME)


@pytest.mark.filterwarnings("ignore:.*Unverified HTTPS request.*")
class TestOpenIssues:
    """Tests for the open-issues command."""

    _ISSUE = apimeta.Issue(title="This is a title", body="This is a body")

    def test_happy_path(self, tmpdir_volume_arg, tmpdir, with_student_repos):
        """Test opening an issue in each student repo."""
        filename = "issue.md"
        text = "{}\n{}".format(self._ISSUE.title, self._ISSUE.body)
        tmpdir.join(filename).write_text(text, encoding="utf-8")

        command = (
            "repobee open-issues -g {} -o {} -i {} -mn {} -s {} -t {} -tb"
        ).format(
            BASE_URL,
            ORG_NAME,
            "{}/{}".format(VOLUME_DST, filename),
            " ".join(MASTER_REPO_NAMES),
            " ".join(STUDENT_TEAM_NAMES),
            TOKEN,
        )

        result = run_in_docker(command, extra_args=tmpdir_volume_arg)

        assert result.returncode == 0
        assert_num_issues(STUDENT_TEAMS, MASTER_REPO_NAMES, 1)
        assert_issues_exist(STUDENT_TEAMS, MASTER_REPO_NAMES, self._ISSUE)


@pytest.mark.filterwarnings("ignore:.*Unverified HTTPS request.*")
class TestCloseIssues:
    """Tests for the close-issues command."""

    def test_closes_only_matched_issues(self, open_issues):
        """Test that close-issues respects the regex."""
        assert len(open_issues) == 2, "expected there to be only 2 open issues"
        close_issue = open_issues[0]
        open_issue = open_issues[1]
        command = (
            "repobee close-issues -g {} -o {} -mn {} -s {} -t {} -r {} -tb"
        ).format(
            BASE_URL,
            ORG_NAME,
            " ".join(MASTER_REPO_NAMES),
            " ".join(STUDENT_TEAM_NAMES),
            TOKEN,
            close_issue.title,
        )

        result = run_in_docker(command)

        assert result.returncode == 0
        assert_issues_exist(
            STUDENT_TEAM_NAMES,
            MASTER_REPO_NAMES,
            close_issue,
            expected_state="closed",
        )
        assert_issues_exist(
            STUDENT_TEAM_NAMES,
            MASTER_REPO_NAMES,
            open_issue,
            expected_state="opened",
        )