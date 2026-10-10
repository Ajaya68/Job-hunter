"""Scoring regression cases, kept offline so calibration never silently drifts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hunt  # noqa: E402

# (title, description, experience, expected: "reject" or minimum score)
CASES = [
    # --- must be rejected -------------------------------------------------
    ("Full Stack .Net Developer", "Develop .NET applications with C# and Angular.", "", "reject"),
    ("Student Internship", "Student intern to write Java code in Spring Boot.", "", "reject"),
    ("Full Stack React Native Developer", "Build React Native apps for Android and iOS.", "", "reject"),
    ("Senior Software Engineer", "Java Spring Boot microservices, 8+ yrs, AWS.", "", "reject"),
    ("Data Management Practitioner", "Data pipelines in Python and machine learning.", "", "reject"),
    ("Java Developer", "Java 11, Spring Boot, Oracle.", "5-9 yrs", "reject"),
    ("UI/UX Designer", "Figma, wireframes.", "", "reject"),
    ("Business Analyst", "Requirements and SAP ABAP.", "", "reject"),
    ("Software Engineer", "Java backend work on internal tooling.", "6-10 yrs", "reject"),
    ("Software Engineer II - Java, Springboot, AWS", "Java 11, Spring Boot, AWS.", "", "reject"),
    ("Software Engineer II, ITC", "Java application support.", "", "reject"),
    ("Senior Software Engineer - Java, APIs, Microservices", "Java, Spring Boot.", "", "reject"),
    # ...but a posting that explicitly spans both levels is a real opening.
    # Senior-first listing: rejected deliberately, see is_senior_only().
    ("Java Developer/Senior Java Developer", "Java Spring Boot Oracle SQL.", "", "reject"),

    # --- must score well --------------------------------------------------
    ("Java Developer", "Java 8, Spring Boot, Spring Data JPA, Oracle, PL/SQL, REST APIs, React. Git, Maven.", "0-3 yrs", 80),
    ("Full Stack Java Developer", "Spring Boot REST, Oracle PL/SQL, React, Bootstrap, Git, Docker.", "0-2 yrs", 85),
    ("Java React Developer (2-3yrs)", "Java Spring Boot REST API with React frontend, Oracle SQL.", "2-3 yrs", 80),
    ("Junior Java Developer", "Spring Boot, JPA, MySQL, REST. Freshers welcome.", "0-1 yrs", 78),
    ("Graduate Software Engineer - Java", "Java, Spring, SQL. Training provided.", "0 yrs", 75),

    # --- correct but too senior / thin, so must stay under the cut-off ----
    # A 4-6 yr seat now fails the fresher gate outright, not just on score.
    ("Java Developer", "Java, Oracle PL/SQL, SQL. Enterprise billing systems.", "4-6 yrs", "reject"),
    # Trainee/intern postings are deliberately excluded: this resume already
    # carries a full-stack training cert and is targeting real developer roles.
    ("Java Trainee", "Java basics, SQL. 6 month training.", "0 yrs", "reject"),

    # --- the fresher ceiling drives these -----------------------------------
    ("Java Developer", "Java, Spring Boot, Oracle.", "2-6 yrs", "reject", 3),
    ("Java Developer", "Java, Spring Boot, Oracle.", "3-8 yrs", "reject", 3),
    ("Java Developer", "Java, Spring Boot, Oracle.", "2-6 yrs", 70, 6),
    ("Java Developer", "Java, Spring Boot, Oracle.", "0-6 yrs", 70, 3),
    ("Java Developer", "Java, Spring Boot, Oracle.", "2-3 yrs", 70, 3),
    ("Java Developer", "Java, Spring Boot, Oracle.", "2-3 yrs", "reject", 2),
    ("Java Developer", "Java, Spring Boot, Oracle.", "2+ yrs", "reject", 3),
    ("Java Developer", "Java, Spring Boot, Oracle.", "Fresher", 70, 3),
    # --- the "Fresher (0-1 yr)" dashboard option, pushed to the scraper ---
    ("Java Developer", "Java, Spring Boot, Oracle.", "0-1 yrs", 70, 1),
    ("Java Developer", "Java, Spring Boot, Oracle.", "0-6 yrs", 70, 1),
    ("Java Developer", "Java, Spring Boot, Oracle.", "Fresher", 70, 1),
    ("Java Developer", "Java, Spring Boot, Oracle.", "2-6 yrs", "reject", 1),
    ("Java Developer", "Java, Spring Boot, Oracle.", "1-3 yrs", 70, 1),
    ("Java Developer", "Java, Spring Boot, Oracle.", "", 70, 3),
]

fails = 0
for case in CASES:
    title, desc, exp, expect = case[:4]
    max_years = case[4] if len(case) > 4 else 3
    job = {"title": title, "text": f"{title} {desc}", "experience": exp, "location": ""}
    reason = hunt.reject_reason(job, max_years)
    if expect == "reject":
        ok = reason is not None
        got = reason or f"score={hunt.score_job(job)[0]}"
    else:
        score, hit, miss = hunt.score_job(job)
        if str(expect).startswith("<"):
            want = int(str(expect)[1:])
            ok = reason is None and score < want
        else:
            ok = reason is None and score >= expect
        got = f"score={score}" + (f" ({reason})" if reason else "")
    fails += not ok
    tag = f"max={max_years}" if len(case) > 4 else ""
    print(f"{'PASS' if ok else 'FAIL'}  {title[:40]:40s} exp={exp or '-':9s} "
          f"expect={str(expect):8s} {tag:9s} got={got}")

print(f"\n{len(CASES) - fails}/{len(CASES)} passed")
sys.exit(1 if fails else 0)
