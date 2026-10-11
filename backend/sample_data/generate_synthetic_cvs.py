"""Generate the ten synthetic CVs in synthetic_cvs/ (PDF) and their manifest.

One CV per seeded candidate. The candidate's name, email, experience and skills come from data/candidates.json, so a CV can never
disagree with its candidate record; the manifest links each file to the candidate by email (the seed key, which survives database
re-creation, unlike an autoincrement id). seed_database.attach_candidate_cvs() reads the manifest and stores the extracted text on
the matching candidate row, verifying that the name printed in the PDF equals the candidate's name.

Every person, company, school and project below is invented. Phone numbers use the fictional 555-01xx range and emails use the
reserved example.com domain. Re-running this script rewrites the PDFs and manifest; it touches nothing else.

Run from backend/:  venv\\Scripts\\python.exe sample_data\\generate_synthetic_cvs.py
"""

import json
import os
import re

from fpdf import FPDF

HERE = os.path.dirname(__file__)
OUT_DIR = os.path.join(HERE, "synthetic_cvs")
CANDIDATES = os.path.join(HERE, "..", "..", "data", "candidates.json")

# Keyed by candidate email. "who" is only used for the filename; "roles" are (title, duration, bullets) newest first.
PROFILES = {
    "aarav.sharma@example.com": dict(
        who="backend_mid", target="Backend Engineer", city="Pune",
        headline="Backend Engineer - data pipelines and REST APIs",
        summary="Backend engineer with {years} years of experience building internal APIs and ETL jobs for an energy analytics company. Comfortable with SQL optimisation and operating services on AWS.",
        skills="Python, FastAPI, Flask, PostgreSQL, SQL tuning, REST APIs, Pandas, Airflow, Docker, AWS (EC2, S3, Lambda), Git",
        roles=[("Backend Engineer, Voltaic Analytics (fictional)", [
                    "Built a FastAPI service that serves meter readings to 40 internal dashboards, with response caching in Redis.",
                    "Rewrote slow reporting queries using window functions and partial indexes; reports went from 40 s to 3 s.",
                    "Maintained Airflow DAGs that ingest 12 GB of sensor data daily into PostgreSQL."]),
               ("Junior Developer, Casa Digital (fictional)", [
                    "Implemented CRUD APIs in Flask for a property-management product and wrote unit tests with pytest."])],
        projects=[("Meter-anomaly detector", "Python job that flags unusual consumption using rolling statistics and posts alerts to Slack."),
                  ("Personal finance API", "FastAPI and PostgreSQL side project with JWT authentication and Alembic migrations.")],
        education="B.Eng. Informatics, Universidade do Douro (fictional)", extra=""),
    "priya.patel@example.com": dict(
        who="frontend_performance", target="Frontend Engineer", city="Ahmedabad",
        headline="Frontend Engineer - React, TypeScript and performance",
        summary="Frontend engineer with {years} years of experience. Focuses on performance budgets and accessible interfaces, and has used Redux and Zustand in production, preferring the simplest tool that fits.",
        skills="React, TypeScript, JavaScript, CSS, Redux, Zustand, Next.js, Vite, Web Vitals, Cypress, Jest",
        roles=[("Frontend Engineer, Nimbus Travel (fictional)", [
                    "Moved a single Redux store to feature-scoped Zustand stores, cutting re-renders on the search page by 60%.",
                    "Set up performance budgets in CI and fixed a hydration mismatch that caused layout shift on server-rendered pages.",
                    "Introduced end-to-end tests covering the booking funnel."]),
               ("Frontend Developer, Vistula Media (fictional)", [
                    "Built news-site components in React and CSS Modules; implemented infinite scroll with intersection observers."])],
        projects=[("Bundle-size watcher", "GitHub Action that comments on pull requests when the bundle grows by more than 5%."),
                  ("Accessible date picker", "Keyboard-first date-range component with ARIA support.")],
        education="B.Tech. Information Technology, Western Institute of Technology (fictional)", extra=""),
    "marcus.j@example.com": dict(
        who="backend_junior", target="Backend Engineer", city="Leipzig",
        headline="Junior Backend Developer",
        summary="Developer with {years} years of experience, including an internship and course projects building web APIs in Python. Eager to learn production practices such as testing, containers and monitoring.",
        skills="Python, SQL, Docker, Flask, FastAPI (learning), SQLite, PostgreSQL basics, Git, unit testing with pytest",
        roles=[("Backend Developer, Kleinwerk Software (fictional)", [
                    "Added Flask endpoints and database migrations to an inventory tool under a senior engineer's review.",
                    "Wrote pytest tests that raised coverage of the orders module from 55% to 80%.",
                    "Containerised the service with Docker and documented the endpoints for the team."]),
               ("Teaching Assistant, Hochschule Elbe (fictional)", [
                    "Ran weekly lab sessions on databases and Python for 30 first-year students; wrote the SQL exercises and marking guide."])],
        projects=[("Campus bike-share API", "Capstone project: FastAPI service with PostgreSQL, booking logic and a Docker Compose setup."),
                  ("Recipe recommender", "Course project using SQLite full-text search and a simple scoring function.")],
        education="B.Sc. Applied Computer Science, Hochschule Elbe (fictional)", extra=""),
    "sofia.garcia@example.com": dict(
        who="fullstack_mid", target="Full Stack Engineer", city="Toronto",
        headline="Full Stack Engineer - Node.js and React",
        summary="Full stack engineer with {years} years of experience shipping features end to end for a healthcare scheduling product, from SQL schemas to React interfaces.",
        skills="JavaScript, React, Node.js, SQL, PostgreSQL, TypeScript, Express, Docker, AWS (EC2, S3, RDS), Git, GitHub Actions",
        roles=[("Full Stack Engineer, CareSlot Health (fictional)", [
                    "Built an appointment-booking flow: Node.js endpoints, PostgreSQL exclusion constraints to prevent double booking, and a React calendar UI.",
                    "Added Dockerised preview environments per pull request using GitHub Actions and AWS.",
                    "Reduced the booking page bundle by 35% by lazy-loading the calendar library."]),
               ("Web Developer, Maple Digital Agency (fictional)", [
                    "Delivered React and Express projects for clients, including a membership portal with billing in test mode."])],
        projects=[("Clinic waitlist app", "React and Node.js app that notifies patients by email when earlier slots open."),
                  ("Schema linter", "Script that checks PostgreSQL migrations for missing indexes on foreign keys.")],
        education="B.Sc. Software Engineering, Great Lakes University (fictional)", extra=""),
    "chen.wei@example.com": dict(
        who="frontend_junior", target="Frontend Engineer", city="Chengdu",
        headline="Junior Frontend Developer",
        summary="Bootcamp graduate with {years} year of freelance experience building small-business websites. Learning React and testing.",
        skills="HTML, CSS, JavaScript, React (hooks, learning), Tailwind CSS, Git, Figma handoff, REST API consumption",
        roles=[("Freelance Web Developer", [
                    "Built five responsive sites for local businesses with HTML, CSS and JavaScript; managed content through a headless CMS.",
                    "Improved one site's mobile load time by compressing images and lazy-loading sections.",
                    "Agreed scope and weekly demos with clients, and fixed browser-specific layout bugs reported after launch.",
                    "Added a contact form with client-side validation and accessible error messages, tested with a screen reader."])],
        projects=[("Weather dashboard", "Small app using a public weather API, with debounced search and local-storage favourites."),
                  ("Todo board", "Drag-and-drop kanban built with plain JavaScript and then rebuilt in React with useReducer."),
                  ("Portfolio site", "Responsive personal site in HTML and CSS Grid with a dark mode toggle stored in local storage.")],
        education="Frontend Development Bootcamp, Sichuan Code School (fictional); B.A. Graphic Design (fictional)", extra=""),
    "yuki.tanaka@example.com": dict(
        who="backend_senior", target="Backend Engineer", city="Osaka",
        headline="Senior Backend Engineer - payments and high-throughput APIs",
        summary="Backend engineer with {years} years of experience designing payment and ledger services in Python. Led the migration of a monolith to FastAPI microservices handling 4 million requests per day, and mentors junior engineers.",
        skills="Python, FastAPI, PostgreSQL, Redis, Celery, Docker, Kubernetes, AWS (ECS, RDS, SQS), SQLAlchemy, pytest, REST API design",
        roles=[("Senior Backend Engineer, Brightledger Payments (fictional)", [
                    "Split a Django monolith into six FastAPI services; cut p95 latency of the checkout API from 900 ms to 210 ms.",
                    "Designed an idempotent payment-capture flow using database unique keys and Redis locks to prevent double charges.",
                    "Introduced Celery workers with SQS for reconciliation jobs; reduced nightly batch time from 5 hours to 50 minutes."]),
               ("Backend Developer, Tandem Logistics (fictional)", [
                    "Built REST endpoints for shipment tracking in Flask and PostgreSQL; added composite indexes that removed a full table scan.",
                    "Wrote the first CI pipeline (GitHub Actions) and Docker images used by all backend teams."])],
        projects=[("Ledgerlite (open-source)", "Double-entry accounting library in Python with property-based tests (Hypothesis) and a FastAPI demo."),
                  ("Rate-limit gateway", "Token-bucket rate limiter backed by Redis scripts, benchmarked at 20k requests per second.")],
        education="B.Sc. Computer Science, Kansai University of Technology (fictional)", extra="AWS Certified Developer - Associate"),
    "emma.thompson@example.com": dict(
        who="frontend_design_systems", target="Frontend Engineer", city="Manchester",
        headline="Frontend Engineer - React, TypeScript and design systems",
        summary="Frontend engineer with {years} years of experience building accessible, performant React applications. Maintains a shared component library used by several product teams.",
        skills="React, TypeScript, JavaScript (ES2022), Tailwind CSS, Jest, React Testing Library, React Query, Storybook, Vite, HTML5, CSS",
        roles=[("Frontend Engineer, Lumen Retail Tech (fictional)", [
                    "Built the product-listing and checkout UI in React and TypeScript; improved Largest Contentful Paint from 3.8 s to 1.9 s with code splitting and image optimisation.",
                    "Created a Storybook-documented component library with 45 components and automated accessibility checks.",
                    "Replaced hand-written fetch logic with React Query, removing a class of stale-data bugs."]),
               ("UI Developer, Pixelhaus Studio (fictional)", [
                    "Delivered marketing sites in JavaScript and Sass with Lighthouse scores above 95."])],
        projects=[("Accessible date picker", "Keyboard-first date-range component with ARIA support, published internally."),
                  ("Realtime order board", "React dashboard using WebSockets and virtualised lists for 10,000 rows.")],
        education="B.Sc. Computing, University of the Northern Quays (fictional)", extra=""),
    "raj.kumar@example.com": dict(
        who="backend_django", target="Backend Engineer", city="Hyderabad",
        headline="Backend Developer - Django and PostgreSQL",
        summary="Backend developer with {years} years of experience building Django applications for an insurance-quotes platform, with a focus on database design and query performance.",
        skills="Python, Django, Django REST Framework, SQL, PostgreSQL, Celery, Redis, Docker basics, Git, pytest",
        roles=[("Backend Developer, Sureline Quotes (fictional)", [
                    "Built the quote-calculation API in Django REST Framework serving 15,000 requests per day.",
                    "Removed N+1 queries with select_related and prefetch_related, cutting the policy list endpoint from 2.4 s to 300 ms.",
                    "Moved PDF generation to Celery workers so the quote endpoint no longer blocks."]),
               ("Software Trainee, Deccan Infosystems (fictional)", [
                    "Maintained a Django admin portal and wrote SQL reports for the operations team."])],
        projects=[("Fleet maintenance tracker", "Django app with role-based permissions and scheduled reminders."),
                  ("Query-plan notes", "Internal write-up comparing PostgreSQL index types on a 5-million-row table.")],
        education="B.Tech. Computer Science, Deccan Technical University (fictional)", extra=""),
    "lisa.anderson@example.com": dict(
        who="fullstack_senior", target="Full Stack Engineer", city="Oslo",
        headline="Senior Full Stack Engineer - platform and product",
        summary="Senior engineer with {years} years of experience across the stack. Designs data models, APIs and UIs for a logistics SaaS product and drives engineering standards across teams.",
        skills="Python, React, JavaScript, SQL, TypeScript, FastAPI, Next.js, PostgreSQL, Redis, Docker, AWS, observability, system design",
        roles=[("Senior Engineer, Fjordline Freight Software (fictional)", [
                    "Designed the multi-tenant PostgreSQL schema (row-level security) and FastAPI layer that serves 300 customers.",
                    "Led the React and Next.js rewrite of the customer portal; cut time-to-interactive by 45% and introduced shared TypeScript API types generated from OpenAPI.",
                    "Created the team's incident process and distributed tracing setup with OpenTelemetry."]),
               ("Software Engineer, Nordhavn Systems (fictional)", [
                    "Built internal tools in Python and JavaScript; migrated a MySQL database to PostgreSQL with zero downtime using logical replication."])],
        projects=[("OpenAPI type sync", "Tool that regenerates TypeScript clients from FastAPI schemas in CI and fails the build on breaking changes."),
                  ("Tenant-isolation tests", "Property-based test suite proving that no query leaks data across tenants.")],
        education="M.Sc. Computer Science, Nordic Institute of Technology (fictional)", extra="AWS Certified Solutions Architect - Associate"),
    "adil.hassan@example.com": dict(
        who="frontend_junior_ts", target="Frontend Engineer", city="Karachi",
        headline="Junior Frontend Developer - JavaScript and TypeScript",
        summary="Developer with {years} years of experience on a small team building a school-fees payment portal front end, moving from JavaScript to TypeScript and growing into testing and accessibility.",
        skills="JavaScript, TypeScript, CSS, HTML, React, Sass, Git, REST APIs, Jest basics",
        roles=[("Frontend Developer, Savanna Edutech (fictional)", [
                    "Implemented fee-statement tables with filtering and CSV export in React and TypeScript.",
                    "Fixed a bug where double-clicking Pay produced duplicate requests by disabling the control and making the handler retry-safe.",
                    "Paired with a senior engineer on code reviews and learned to split large pull requests into reviewable pieces."]),
               ("IT Support Intern, Indus Valley University (fictional)", [
                    "Supported the registrar's office systems and automated a weekly enrolment export with a small script."])],
        projects=[("Bus-timetable PWA", "Offline-capable progressive web app using service workers and cached timetables."),
                  ("Study-group finder", "React front end with form validation and keyboard-accessible filters.")],
        education="B.Sc. Information Systems, Indus Valley University (fictional)", extra=""),
}


def _durations(count: int, years: int) -> list[str]:
    """Spread the candidate's years over the roles (newest first) so dates agree with experience_years."""
    if count == 1:
        return [f"{max(years, 1)} yr" + ("s" if years > 1 else "")]
    first = max(1, round(years * 0.65))
    rest = max(1, years - first)
    return [f"{first} yr" + ("s" if first > 1 else ""), f"{rest} yr" + ("s" if rest > 1 else "")]


def build_cv(candidate: dict, profile: dict) -> dict:
    years = candidate["experience_years"]
    skills = [s.strip() for s in profile["skills"].split(",")]
    present = " ".join(skills).lower()
    missing = [s.strip() for s in candidate["skills"].split(",") if s.strip().lower() not in present]
    slug = re.sub(r"[^a-z0-9]+", "_", candidate["name"].lower()).strip("_")
    return {
        "name": candidate["name"], "email": candidate["email"], "headline": profile["headline"], "target": profile["target"],
        "contact": f"{candidate['email']} | 555-01{0:02d} | {profile['city']}",
        "summary": profile["summary"].format(years=years),
        "skills": profile["skills"] + (", " + ", ".join(missing) if missing else ""),
        "experience": [(title, d, bullets) for (title, bullets), d in zip(profile["roles"], _durations(len(profile["roles"]), years))],
        "projects": profile["projects"], "education": profile["education"], "extra": profile["extra"],
        "slug": slug,
    }


def build_pdf(cv: dict) -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(18, 16, 18)
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin

    def heading(text):
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(width, 7, text.upper(), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(150, 150, 150)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + width, pdf.get_y())
        pdf.ln(2)

    def body(text, style=""):
        pdf.set_font("Helvetica", style, 10)
        pdf.multi_cell(width, 5, text, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(width, 9, cv["name"], new_x="LMARGIN", new_y="NEXT")
    body(cv["headline"], "B")
    body(cv["contact"])
    body(f"Target role: {cv['target']}  |  SYNTHETIC SAMPLE CV - fictional person, for software testing only", "I")

    heading("Professional summary")
    body(cv["summary"])
    heading("Technical skills")
    body(cv["skills"])
    heading("Experience")
    for title, duration, bullets in cv["experience"]:
        body(f"{title}   [{duration}]", "B")
        for bullet in bullets:
            body(f"  - {bullet}")
        pdf.ln(1)
    heading("Projects")
    for name, description in cv["projects"]:
        body(name, "B")
        body(f"  {description}")
    heading("Education")
    body(cv["education"])
    if cv["extra"]:
        heading("Certifications")
        body(cv["extra"])
    return bytes(pdf.output())


def main():
    with open(CANDIDATES, encoding="utf-8") as f:
        candidates = json.load(f)
    assert len(candidates) == len(PROFILES) == 10 and {c["email"] for c in candidates} == set(PROFILES)
    os.makedirs(OUT_DIR, exist_ok=True)
    for old in os.listdir(OUT_DIR):
        if old.endswith(".pdf"):
            os.remove(os.path.join(OUT_DIR, old))
    manifest = []
    for order, candidate in enumerate(candidates, 1):
        profile = PROFILES[candidate["email"]]
        cv = build_cv(candidate, profile)
        cv["contact"] = cv["contact"].replace("555-0100", f"555-01{order:02d}")
        file_name = f"{order:02d}_{profile['who']}_{cv['slug']}.pdf"
        with open(os.path.join(OUT_DIR, file_name), "wb") as f:
            f.write(build_pdf(cv))
        manifest.append({"candidate_email": candidate["email"], "candidate_name": candidate["name"], "seed_order": order, "file": file_name})
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print(f"Wrote {len(manifest)} synthetic CVs and manifest.json to {OUT_DIR}")


if __name__ == "__main__":
    main()
