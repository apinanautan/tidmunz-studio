# Pick a random hairstyle for a CC5 job from the checked hair pool.
#   python cc5_pick_hair.py <job_folder> <male|female> <child|adult|middle|old>
# Pool: <program>/snapgen_data/cc5_hair_pool.json (only hairs checked by eye).
# Hairs already used by other jobs are skipped while unused ones remain, so
# characters in the same story don't share a hairstyle. Hair colour follows age:
# black for child/adult, gray for middle-aged, white for old.
import json, os, random, shutil, sys

PROGRAM = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(PROGRAM, "snapgen_data")
JOBS = os.path.join(DATA, "cc5_jobs")


def used_hairs(skip_job):
    used = set()
    for name in os.listdir(JOBS):
        job = os.path.join(JOBS, name, "job.json")
        if name == skip_job or not os.path.isfile(job):
            continue
        source = json.load(open(job, encoding="utf-8")).get("hair_source")
        if source:
            used.add(source)
    return used


def pick(job_dir, gender, age):
    pool = json.load(open(os.path.join(DATA, "cc5_hair_pool.json"), encoding="utf-8"))
    group = "%s_%s" % (gender, {"middle": "old"}.get(age, age))
    if age == "middle" and gender == "female":
        group = "female_adult"
    choices = [h for h in pool["hairs"] if group in h["groups"]]
    if not choices:
        raise SystemExit("no hair for " + group)
    used = used_hairs(os.path.basename(job_dir))
    fresh = [h for h in choices if h["path"] not in used] or choices
    hair = random.choice(fresh)
    shutil.copy(hair["path"], os.path.join(job_dir, "hair.rlHair"))
    job_file = os.path.join(job_dir, "job.json")
    job = json.load(open(job_file, encoding="utf-8")) if os.path.isfile(job_file) else {}
    items = [i for i in job.get("items", []) if not i.lower().endswith((".rlhair", ".cchair"))]
    job["items"] = ["hair.rlHair"] + items
    job["hair_source"] = hair["path"]
    job["hair_rgb"] = pool["colors"][age]
    json.dump(job, open(job_file, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return hair["name"]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(pick(os.path.abspath(sys.argv[1]), sys.argv[2], sys.argv[3]))
