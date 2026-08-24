from morning_digest.collectors import (
    _job_postings_from_json_ld,
    fetch_jobicy,
    fetch_remote_ok,
    fetch_remotive,
    fetch_swiss_ai_job,
    fetch_swiss_dev_jobs,
)


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return self

    def json(self):
        return self.data

    @property
    def text(self):
        return self.data


class Client:
    def __init__(self, data):
        self.data = data

    def get(self, _url, **_kwargs):
        return Response(self.data)


def test_jobicy_mapping():
    jobs = fetch_jobicy(Client({"jobs": [{
        "jobTitle": "ML Engineer", "url": "https://jobicy.example/1",
        "pubDate": "2026-08-12T10:00:00Z", "jobDescription": "<p>Build models</p>",
        "companyName": "Acme", "jobGeo": "Europe",
    }]}))
    assert (jobs[0].source, jobs[0].title, jobs[0].remote) == ("Jobicy", "ML Engineer", True)


def test_remotive_mapping():
    jobs = fetch_remotive(Client({"jobs": [{
        "title": "AI Intern", "url": "https://remotive.example/1",
        "publication_date": "2026-08-12T10:00:00Z", "description": "Research",
        "company_name": "Acme", "candidate_required_location": "EMEA",
    }]}))
    assert (jobs[0].source, jobs[0].location) == ("Remotive", "EMEA")


def test_remote_ok_skips_metadata_record():
    jobs = fetch_remote_ok(Client([
        {"legal": "metadata"},
        {"position": "Data Scientist", "url": "https://remoteok.example/1",
         "date": "2026-08-12T10:00:00Z", "company": "Acme"},
    ]))
    assert len(jobs) == 1
    assert (jobs[0].source, jobs[0].location) == ("Remote OK", "Worldwide")


def test_extracts_nested_job_posting_json_ld():
    html = '''<script type="application/ld+json">{"@type":"ItemList","itemListElement":[
      {"item":{"@type":"JobPosting","title":"ML Intern"}}]}</script>'''
    assert _job_postings_from_json_ld(html)[0]["title"] == "ML Intern"


def test_swiss_ai_job_mapping():
    html = '''<a href="/jobs/acme-zurich-ml-intern-1"><img alt="Acme">
      <span class="uppercase tracking-widest">Junior</span>
      <span class="uppercase tracking-widest">2 days ago</span>
      <h3>Machine Learning Intern</h3></a>'''
    jobs = fetch_swiss_ai_job(Client(html))
    assert len(jobs) == 1
    assert (jobs[0].source, jobs[0].author, jobs[0].company) == (
        "SwissAIJob", "Junior", "Acme"
    )


def test_swiss_dev_jobs_mapping():
    jobs = fetch_swiss_dev_jobs(Client([{
        "name": "Junior ML Engineer", "redirectJobUrl": "https://acme.example/apply",
        "activeFrom": "2026-08-12T10:00:00Z", "technologies": ["Python", "ML"],
        "expLevel": "Junior", "company": "Acme", "actualCity": "Bern",
        "workplace": "hybrid",
    }]))
    assert (jobs[0].source, jobs[0].remote, jobs[0].location) == (
        "SwissDevJobs", True, "Bern"
    )
