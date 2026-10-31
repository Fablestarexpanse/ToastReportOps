"""Safe model downloads: official names only, pinned digests, fit checks, no surprises."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from toast import modelsetup as ms

GB = 1_000_000_000
MANIFEST_BODY = b'{"layers":[{"size":1000000000}]}'
DIGEST = hashlib.sha256(MANIFEST_BODY).hexdigest()


def manifest(**over):
    entry = {"role": "chat", "manifest_sha256": DIGEST, "size_bytes": 5 * GB}
    return {
        "schema": 1,
        "min_ollama_version": "0.32.0",
        "models": {
            "chat:9b": {**entry, **over},
            "emb:latest": {"role": "embed", "dim": 4, "manifest_sha256": DIGEST, "size_bytes": GB},
        },
        "profiles": {"starter": {"models": ["chat:9b", "emb:latest"]}},
    }


class FakeOllama(ms.Ollama):
    """Ollama over an in-memory transport; `store` maps name -> digest."""

    def __init__(self, store=None, version="0.32.3", pull_events=None, dim=4, reply="ready"):
        self.store = dict(store or {})
        self.pulled: list[str] = []
        self.events = pull_events
        self.dim, self.reply = dim, reply
        super().__init__("http://ollama.test", transport=httpx.MockTransport(self._route(version)))

    def _route(self, version):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            body = json.loads(request.content) if request.content else {}
            if path == "/api/version":
                return httpx.Response(200, json={"version": version})
            if path == "/api/tags":
                return httpx.Response(
                    200,
                    json={"models": [{"name": n, "digest": d} for n, d in self.store.items()]},
                )
            if path == "/api/pull":
                name = body["model"]
                self.pulled.append(name)
                events = self.events or [
                    {"status": "pulling", "total": 100, "completed": 50},
                    {"status": "pulling", "total": 100, "completed": 100},
                    {"status": "success"},
                ]
                if not any("error" in e for e in events):
                    self.store[name] = DIGEST
                return httpx.Response(200, content="\n".join(json.dumps(e) for e in events))
            if path == "/api/embed":
                return httpx.Response(200, json={"embeddings": [[0.1] * self.dim]})
            if path == "/api/chat":
                return httpx.Response(200, json={"message": {"content": self.reply}})
            return httpx.Response(404)

        return handler


def registry(digest=DIGEST):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "registry.ollama.ai"
        return httpx.Response(200, content=MANIFEST_BODY if digest == DIGEST else b'{"layers":[]}')

    return ms.Registry(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------- names


@pytest.mark.parametrize(
    "bad",
    [
        "hf.co/DavidAU/Qwen3.5-9B-Heretic-GGUF:Q6_K",
        "someuser/model:latest",
        "../../etc/passwd",
        "https://evil.example/m",
        "Model With Spaces",
        "",
    ],
)
def test_community_and_odd_names_are_refused(bad):
    with pytest.raises(ms.ModelSetupError, match="official Ollama library"):
        ms.check_name(bad)


def test_official_names_get_a_default_tag():
    assert ms.check_name("nomic-embed-text") == "nomic-embed-text:latest"
    assert ms.check_name("qwen3.5:9b") == "qwen3.5:9b"


def test_shipped_manifest_is_well_formed_and_fully_pinned():
    m = ms.load_manifest()
    for name, entry in m["models"].items():
        assert len(entry["manifest_sha256"]) == 64, name
        assert entry["size_bytes"] > 0 and entry["role"] in ("chat", "embed")
    for prof in m["profiles"].values():
        assert set(prof["models"]) <= set(m["models"])
    assert m["profiles"]["starter"]["models"][0] == "qwen3.5:9b"


def test_version_parsing():
    assert ms.parse_version("0.32.3") > ms.parse_version("0.32.0")
    assert ms.parse_version("0.31.2") < ms.parse_version("0.32.0")
    assert ms.parse_version("0.40.4-rc1") == (0, 40, 4)


# -------------------------------------------------------------------- plan


def test_plan_marks_present_pull_and_too_big():
    m = manifest()
    items = {
        i.name: i for i in ms.plan(m, ["chat:9b", "emb:latest"], {"emb:latest": "sha256:" + DIGEST})
    }
    assert items["emb:latest"].action == "present" and items["chat:9b"].action == "pull"
    small = {i.name: i.action for i in ms.plan(m, ["chat:9b"], {}, vram_gb=4)}
    assert small["chat:9b"] == "too_big"
    ok = {i.name: i.action for i in ms.plan(m, ["chat:9b"], {}, vram_gb=12)}
    assert ok["chat:9b"] == "pull"


def test_installed_copy_that_differs_from_the_pin_is_replanned():
    (item,) = ms.plan(manifest(), ["chat:9b"], {"chat:9b": "f" * 64})
    assert item.action == "pull" and "differs" in item.reason


def test_unpinned_models_are_never_installed():
    m = manifest()
    del m["models"]["chat:9b"]["manifest_sha256"]
    (item,) = ms.plan(m, ["chat:9b"], {})
    assert item.action == "unpinned"


def test_embedding_models_are_not_vram_gated():
    (item,) = ms.plan(manifest(), ["emb:latest"], {}, vram_gb=0.5)
    assert item.action == "pull"


# ----------------------------------------------------------------- install


def test_install_downloads_verifies_and_smoke_tests():
    ollama, lines = FakeOllama(), []
    code = ms.install(manifest(), ["chat:9b", "emb:latest"], ollama, registry(), out=lines.append)
    assert code == 0 and ollama.pulled == ["chat:9b", "emb:latest"]
    text = "\n".join(lines)
    assert "50%" in text and "4-dim embedding ok" in text and "answered" in text


def test_nothing_is_downloaded_when_the_tag_changed_upstream():
    ollama = FakeOllama()
    with pytest.raises(ms.ModelSetupError, match="changed upstream"):
        ms.install(manifest(), ["chat:9b"], ollama, registry(digest="other"), out=lambda s: None)
    assert ollama.pulled == []


def test_already_installed_and_verified_models_are_not_downloaded_again():
    ollama = FakeOllama(store={"chat:9b": DIGEST, "emb:latest": DIGEST})
    ms.install(manifest(), ["chat:9b", "emb:latest"], ollama, registry(), out=lambda s: None)
    assert ollama.pulled == []


def test_too_old_ollama_is_refused_with_the_fix():
    with pytest.raises(ms.ModelSetupError, match="too old"):
        ms.install(manifest(), ["chat:9b"], FakeOllama(version="0.30.7"), registry())


def test_post_download_mismatch_warns_and_never_deletes():
    class Swapped(FakeOllama):
        def _route(self, version):
            inner = super()._route(version)

            def handler(request):
                resp = inner(request)
                if request.url.path == "/api/pull":
                    self.store[json.loads(request.content)["model"]] = "e" * 64
                assert request.method != "DELETE"
                return resp

            return handler

    lines = []
    code = ms.install(manifest(), ["chat:9b"], Swapped(), registry(), out=lines.append)
    assert code == 3 and any("WARNING" in line and "Not deleted" in line for line in lines)


def test_disk_full_gets_a_plain_message():
    ev = [{"error": "write /root/.ollama: no space left on device"}]
    with pytest.raises(ms.ModelSetupError, match="not enough disk space"):
        ms.install(
            manifest(), ["chat:9b"], FakeOllama(pull_events=ev), registry(), out=lambda s: None
        )


def test_wrong_embedding_size_is_reported():
    lines = []
    code = ms.install(manifest(), ["emb:latest"], FakeOllama(dim=7), registry(), out=lines.append)
    assert code == 3 and any("expected 4" in line for line in lines)


def test_empty_chat_reply_is_reported():
    code = ms.install(manifest(), ["chat:9b"], FakeOllama(reply=""), registry(), out=lambda s: None)
    assert code == 3


def test_unreachable_ollama_has_a_helpful_message():
    dead = ms.Ollama("http://127.0.0.1:9")
    with pytest.raises(ms.ModelSetupError, match="Is the stack running"):
        dead.version()


# -------------------------------------------------------------- pin / status


def test_pin_records_digest_and_size_and_reports_changes():
    m = manifest()
    m["models"]["chat:9b"].pop("manifest_sha256")
    lines = []
    ms.pin(m, registry(), out=lines.append)
    assert m["models"]["chat:9b"]["manifest_sha256"] == DIGEST
    assert m["models"]["chat:9b"]["size_bytes"] == GB
    assert any("new" in line for line in lines) and any("unchanged" in line for line in lines)


def test_registry_rejects_a_corrupt_response():
    def handler(request):
        return httpx.Response(
            200, content=MANIFEST_BODY, headers={"ollama-content-digest": "0" * 64}
        )

    with pytest.raises(ms.ModelSetupError, match="corrupt"):
        ms.Registry(transport=httpx.MockTransport(handler)).manifest("chat:9b")


def test_status_flags_unpinned_configured_model_and_differing_copies():
    lines = []
    code = ms.status(
        manifest(),
        FakeOllama(store={"chat:9b": "f" * 64}),
        "hf.co/someone/model:Q6_K",
        out=lines.append,
    )
    text = "\n".join(lines)
    assert code == 3 and "DIFFERENT FROM PIN" in text and "unverified source" in text


def test_chat_model_command_names_each_profiles_chat_model(capsys):
    for profile, want in (("starter", "qwen3.5:9b"), ("small-8gb", "qwen3.5:4b")):
        assert ms.main(["chat-model", "--profile", profile]) == 0
        assert capsys.readouterr().out.strip() == want


def test_plan_says_plainly_when_no_chat_model_fits():
    items = ms.plan(manifest(), ["chat:9b", "emb:latest"], {}, vram_gb=5)
    text = ms.format_plan(items)
    assert "No chat model fits" in text and "small-8gb" in text
    ok = ms.format_plan(ms.plan(manifest(), ["chat:9b", "emb:latest"], {}, vram_gb=24))
    assert "No chat model fits" not in ok


def test_status_lists_what_is_needed_and_counts_optional_models(capsys):
    m = manifest()
    m["models"]["extra:27b"] = {"role": "chat", "manifest_sha256": DIGEST, "size_bytes": GB}
    m["profiles"]["starter"] = {"models": ["chat:9b", "emb:latest"]}
    lines = []
    ms.status(m, FakeOllama(), "chat:9b", out=lines.append)
    text = "\n".join(lines)
    assert "chat:9b" in text and "emb:latest" in text and "extra:27b" not in text
    assert "1 optional comparison models not installed" in text
    assert "Install Models.cmd" in text
