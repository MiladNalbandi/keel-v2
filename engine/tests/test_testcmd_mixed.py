"""keel 0.22.0 a JVM build file next to a Python project (issue #70): a pom.xml with no Java or Kotlin code beside a
Django app made flows run Maven against the app's Python criteria."""
from keel_engine.tools import testcmd


def files(root, names):
    for n, text in names.items():
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return str(root)


def test_a_pom_without_java_next_to_django_runs_djangos_tests(tmp_path):
    root = files(tmp_path, {"pom.xml": "<project/>", "manage.py": "", "shop/models.py": "", "shop/tests.py": ""})
    assert testcmd.jvm_build_without_code(root)
    assert testcmd._detect_in(root) == "python manage.py test"


def test_pytest_settings_keep_pytest(tmp_path):
    root = files(tmp_path, {"pom.xml": "<project/>", "manage.py": "", "pytest.ini": "[pytest]\nDJANGO_SETTINGS_MODULE=x\n"})
    assert testcmd._detect_in(root) == "python -m pytest -q"


def test_a_real_jvm_project_keeps_maven(tmp_path):
    root = files(tmp_path, {"pom.xml": "<project/>", "requirements.txt": "", "src/main/java/App.java": "class App {}"})
    assert not testcmd.jvm_build_without_code(root)
    assert testcmd._detect_in(root) == "mvn -q test"
    root2 = files(tmp_path / "g", {"gradlew": "", "src/main/kotlin/A.kt": ""})
    assert testcmd._detect_in(root2) == "./gradlew -q test"
