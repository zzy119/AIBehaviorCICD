#!/usr/bin/env python3
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import os

from behavior_release_manager import db, seed, services, templates


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"


def prepare():
    conn = db.connect()
    db.init_db(conn)
    seed.seed(conn)
    conn.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, path, fragment=""):
        if fragment:
            path = "{}#{}".format(path, fragment)
        self.send_response(303)
        self.send_header("Location", path)
        self.end_headers()

    def read_form(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        parsed = parse_qs(raw)
        return {key: values[-1] for key, values in parsed.items()}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/static/"):
            return self.serve_static(parsed.path)

        conn = db.connect()
        try:
            if parsed.path == "/cases":
                self.send_html(templates.render_cases(services.list_eval_cases(conn)))
                return
            if parsed.path == "/":
                query = parse_qs(parsed.query)
                candidate = query.get("candidate", [None])[0]
                message = query.get("message", [""])[0]
                state = services.dashboard_state(conn, candidate)
                self.send_html(templates.render_dashboard(state, message))
                return
            self.send_error(404)
        finally:
            conn.close()

    def serve_static(self, path):
        target = (STATIC_DIR / path.replace("/static/", "", 1)).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists():
            self.send_error(404)
            return
        content = target.read_bytes()
        content_type = "text/css" if target.suffix == ".css" else "text/plain"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        parsed = urlparse(self.path)
        form = self.read_form()
        conn = db.connect()
        try:
            try:
                if parsed.path == "/candidate/update":
                    candidate_id = form["candidate_id"]
                    services.update_candidate(conn, candidate_id, form)
                    self.redirect(
                        "/?candidate={}&message=Candidate saved. Eval evidence may be stale.".format(candidate_id),
                        "behavior-config",
                    )
                    return
                if parsed.path == "/eval/run":
                    candidate_id = form["candidate_id"]
                    run_id = services.run_eval(conn, candidate_id)
                    self.redirect(
                        "/?candidate={}&message=Eval run {} completed.".format(candidate_id, run_id),
                        "eval-comparison",
                    )
                    return
                if parsed.path == "/demo/reset":
                    services.reset_demo_data(conn)
                    seed.seed(conn)
                    self.redirect("/?message=Demo data reset.")
                    return
                if parsed.path == "/pipeline/advance":
                    candidate_id = form["candidate_id"]
                    stage = services.advance_pipeline(conn, candidate_id)
                    self.redirect(
                        "/?candidate={}&message=Advanced pipeline to {}.".format(candidate_id, stage),
                        "pipeline",
                    )
                    return
                if parsed.path == "/pipeline/rollback":
                    candidate_id = form["candidate_id"]
                    reason = form.get("reason", "Reviewer requested rollback.")
                    services.rollback_pipeline(conn, candidate_id, reason)
                    self.redirect(
                        "/?candidate={}&message=Rolled back pipeline stage.",
                        "pipeline",
                    )
                    return
                if parsed.path == "/reject":
                    candidate_id = form["candidate_id"]
                    services.reject_candidate(conn, candidate_id)
                    self.redirect("/?message=Candidate rejected.", "pipeline")
                    return
                if parsed.path == "/weights/update":
                    services.update_weights(conn, form)
                    self.redirect("/?message=Feedback weights updated.", "feedback")
                    return
            except Exception as exc:
                candidate_id = form.get("candidate_id", "")
                suffix = "&candidate={}".format(candidate_id) if candidate_id else ""
                anchor_by_path = {
                    "/candidate/update": "behavior-config",
                    "/eval/run": "eval-comparison",
                    "/pipeline/advance": "pipeline",
                    "/pipeline/rollback": "pipeline",
                    "/reject": "pipeline",
                    "/weights/update": "feedback",
                }
                self.redirect(
                    "/?message={}{}".format(str(exc).replace(" ", "+"), suffix),
                    anchor_by_path.get(parsed.path, ""),
                )
                return
            self.send_error(404)
        finally:
            conn.close()


def main():
    prepare()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("AI Behavior Release Manager running at http://127.0.0.1:{}".format(port))
    server.serve_forever()


if __name__ == "__main__":
    main()
