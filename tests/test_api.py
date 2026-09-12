def test_ask_happy_path(client, adapter):
    r = client.post("/ask", json={"instruction": "Explain simply", "input": "def f(): pass"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["response"] == "fake answer"
    prompt = adapter.calls[0][0]
    assert "Explain simply" in prompt and "--- INPUT ---" in prompt


def test_requires_token(client):
    r = client.post("/ask", json={"instruction": "x"}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_rejects_non_loopback_host(client):
    r = client.post("/ask", json={"instruction": "x"}, headers={"Host": "evil.example.com"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN_HOST"


def test_input_size_cap(client):
    r = client.post("/ask", json={"instruction": "x", "input": "y" * 2000})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "INPUT_TOO_LARGE"


def test_adapter_errors_map_to_codes(client, adapter):
    adapter.raise_code = "CHATGPT_NOT_AUTHENTICATED"
    r = client.post("/ask", json={"instruction": "x"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "CHATGPT_NOT_AUTHENTICATED"
    assert r.json()["error"]["hint"]


def test_async_job_flow(client):
    r = client.post("/ask?async=1", json={"instruction": "x"})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    for _ in range(50):
        got = client.get(f"/jobs/{job_id}").json()
        if got["status"] == "done":
            break
    assert got["response"] == "fake answer"


def test_unknown_job(client):
    assert client.get("/jobs/j_nope").status_code == 404


def test_cancel_queued_job(client, adapter):
    adapter.delay = 5
    first = client.post("/ask?async=1", json={"instruction": "slow"}).json()["job_id"]
    second = client.post("/ask?async=1", json={"instruction": "queued"}).json()["job_id"]
    assert client.delete(f"/jobs/{second}").json()["status"] in ("cancelled", "running", "done")
    client.delete(f"/jobs/{first}")


def test_health_needs_no_token(client):
    r = client.get("/health", headers={"Authorization": ""})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_dom_probe(client):
    assert client.get("/health/dom").json()["status"] == "ok"
