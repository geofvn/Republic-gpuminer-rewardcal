import requests
import csv
import time
import math
import json
import os
from collections import defaultdict
from bech32 import bech32_decode, bech32_encode, convertbits
from datetime import datetime, timedelta, timezone

def rai_to_valoper(rai_address: str) -> str:
    try:
        hrp, data = bech32_decode(rai_address)
        if hrp is None:
            return None
        data8 = convertbits(data, 5, 8, False)
        return bech32_encode("raivaloper", convertbits(data8, 8, 5, True))
    except:
        return None

API_JOBS = "https://yaci-explorer-apis.fly.dev/compute_jobs"
API_VALIDATORS = "https://yaci-explorer-apis.fly.dev/validators"
CACHE_FILE = "job_cache.json"
LIMIT_PER_PAGE = 500
TOTAL_POOL = 1_600_000
MAX_PER_WALLET = TOTAL_POOL * 0.15

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

def get_validators():
    try:
        r = requests.get(API_VALIDATORS, timeout=20)
        r.raise_for_status()
        validators = r.json()
        valoper_to_moniker = {}
        bonded = set()
        for v in validators:
            valoper = v.get("operator_address")
            moniker = v.get("moniker")
            if valoper:
                if moniker:
                    valoper_to_moniker[valoper] = moniker
                if v.get("status") == "BOND_STATUS_BONDED":
                    bonded.add(valoper)
        return valoper_to_moniker, bonded
    except:
        return {}, set()

def calculate_longest_streak(active_days):
    if not active_days:
        return 0
    sorted_days = sorted(datetime.fromisoformat(d) for d in active_days)
    longest = 1
    current = 1
    for i in range(1, len(sorted_days)):
        if (sorted_days[i] - sorted_days[i-1]).days == 1:
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    return longest

def main():
    print("=" * 120)
    print("   Republic Testnet Weekly Payout System (6 Scores + RAI Distribution)")
    print("=" * 120 + "\n")

    # Interactive date selection
    custom = input("Would you like to select a custom date interval? (YES/NO, default=last 7 days): ").strip().upper()
    if custom == "YES":
        start_str = input("Enter start date (YYYY-MM-DD): ").strip()
        end_str = input("Enter end date (YYYY-MM-DD): ").strip()
        try:
            start_date = datetime.fromisoformat(start_str + "T00:00:00+00:00")
            end_date = datetime.fromisoformat(end_str + "T23:59:59+00:00")
            if (end_date - start_date).days + 1 > 7:
                print("❌ Maximum interval is 7 days. Using last 7 days instead.")
                end_date = datetime.now(timezone.utc)
                start_date = end_date - timedelta(days=7)
        except:
            print("Invalid dates. Using last 7 days.")
            end_date = datetime.now(timezone.utc)
            start_date = end_date - timedelta(days=7)
    else:
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=7)

    print(f"📅 Processing from {start_date.date()} to {end_date.date()} (partial days OK)")

    # Cache handling
    cache = load_cache()
    cache_key = f"{start_date.date()}_{end_date.date()}"
    if cache_key in cache:
        print("✅ Using cached data for this period.")
        jobs = cache[cache_key]
    else:
        jobs = []
        offset = 0
        page = 1
        while True:
            print(f"📥 Fetching page {page} ...", end=" ")
            params = {
                "created_at": f"gte.{start_date.isoformat()}",
                "created_at": f"lte.{end_date.isoformat()}",
                "limit": LIMIT_PER_PAGE,
                "offset": offset
            }
            try:
                r = requests.get(API_JOBS, params=params, timeout=30)
                r.raise_for_status()
                page_jobs = r.json()
                print(f"received {len(page_jobs)} jobs")
                jobs.extend(page_jobs)
                if len(page_jobs) < LIMIT_PER_PAGE:
                    break
                offset += LIMIT_PER_PAGE
                page += 1
                time.sleep(0.4)
            except Exception as e:
                print(f"❌ Error: {e}")
                break
        cache[cache_key] = jobs
        save_cache(cache)

    # Validators
    valoper_to_moniker, bonded_validators = get_validators()

    # Process data
    creator_stats = defaultdict(lambda: {"submitted": 0, "completed": 0})
    worker_stats = defaultdict(lambda: {
        "completed": 0, "attempted": 0, "active_hours": set(), "active_days": set(), "hour_of_day": set()
    })

    for job in jobs:
        creator = job.get("creator")
        processor = job.get("target_validator")
        status = job.get("status")
        result_tx = job.get("result_tx_hash")
        updated_at = job.get("updated_at") or job.get("result_time")

        if creator:
            creator_stats[creator]["submitted"] += 1
            if status == "COMPLETED" and result_tx:
                creator_stats[creator]["completed"] += 1

        if processor and status == "COMPLETED" and result_tx and updated_at:
            worker_stats[processor]["attempted"] += 1
            worker_stats[processor]["completed"] += 1

            try:
                dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                day_key = dt.strftime("%Y-%m-%d")
                hour_key = dt.strftime("%Y-%m-%d %H")
                hour_of_day = dt.hour

                worker_stats[processor]["active_days"].add(day_key)
                worker_stats[processor]["active_hours"].add(hour_key)
                worker_stats[processor]["hour_of_day"].add(hour_of_day)
            except:
                pass

    # Build results
    results = []
    total_network_effort = sum(w["completed"] for w in worker_stats.values())

    for rai_addr, cstats in creator_stats.items():
        valoper = rai_to_valoper(rai_addr)
        wstats = worker_stats.get(valoper, {"completed": 0, "attempted": 0, "active_hours": set(), "active_days": set(), "hour_of_day": set()}) if valoper else {"completed": 0, "attempted": 0, "active_hours": set(), "active_days": set(), "hour_of_day": set()}

        submitted = cstats["submitted"]
        creation_comp = cstats["completed"]
        effort_comp = wstats["completed"]
        attempted = wstats["attempted"]

        creation_rate = creation_comp / submitted if submitted > 0 else 0.0
        success_rate = effort_comp / attempted if attempted > 0 else 0.0

        job_creation_score = 1.00 + min(0.08, creation_rate * 0.08)

        if effort_comp < 5000:
            effort_score = 0.0
        else:
            effort_score = math.sqrt(effort_comp) * success_rate

        unique_hours = len(wstats["active_hours"])
        presence_score = (unique_hours / 168.0) * success_rate if unique_hours > 0 else 0.0

        # Steadiness Option B - Correct implementation with longest streak
        active_days_list = sorted(wstats["active_days"])
        unique_days = len(active_days_list)
        longest_streak = calculate_longest_streak(active_days_list)
        steadiness_score = (unique_days / 7.0) * (longest_streak / 7.0)

        unique_hod = len(wstats["hour_of_day"])
        if effort_comp >= 5000:
            helpfulness_score = 1.00 + (unique_hod / 24.0) * 0.25 * success_rate
        else:
            helpfulness_score = 1.00

        # Builder Option C
        is_bonded = valoper in bonded_validators if valoper else False
        uptime_proxy = min(1.0, effort_comp / 5000.0) if is_bonded else 0.0
        contribution_share = effort_comp / total_network_effort if total_network_effort > 0 else 0.0
        builder_multiplier = 1.00 + (uptime_proxy * 0.05) + (contribution_share * 0.05)
        builder_multiplier = min(1.10, builder_multiplier)

        final_score = effort_score * job_creation_score * presence_score * steadiness_score * helpfulness_score * builder_multiplier

        # Display name
        moniker = valoper_to_moniker.get(valoper, "")
        display_name = moniker if moniker else f"{rai_addr[:12]}...{rai_addr[-8:]}"

        results.append({
            "rank": 0,
            "display_name": display_name,
            "address": rai_addr,
            "jobs_submitted": submitted,
            "creation_completed": creation_comp,
            "effort_completed": effort_comp,
            "success_rate": round(success_rate, 4),
            "job_creation_score": round(job_creation_score, 4),
            "effort_score": round(effort_score, 4),
            "presence_score": round(presence_score, 4),
            "steadiness_score": round(steadiness_score, 4),
            "helpfulness_score": round(helpfulness_score, 4),
            "builder_multiplier": round(builder_multiplier, 4),
            "final_score": round(final_score, 4)
        })

    results.sort(key=lambda x: x["final_score"], reverse=True)
    for i, r in enumerate(results):
        r["rank"] = i + 1

    # Legend
    print("\n" + "═" * 90)
    print("Meaning of the abbreviated words")
    print("═" * 90)
    legend = [
        ("Sub", "Jobs Submitted - Number of jobs this wallet created"),
        ("CrC", "Creation Completed - How many created jobs reached COMPLETED"),
        ("EfC", "Effort Completed - How many jobs processed on GPU with result submitted"),
        ("SR", "Success Rate - Completed / Attempted as processor"),
        ("JC", "Job Creation Score (1.00 - 1.08)"),
        ("Eff", "Effort Score (0 if < 5,000 processed jobs)"),
        ("Pres", "Presence Score (only from processed jobs)"),
        ("Steady", "Steadiness Score (unique days × longest streak)"),
        ("Help", "Helpfulness Score (hour spread × success rate, min 5k jobs)"),
        ("Build", "Builder Bonus Option C (uptime proxy + contribution share)"),
        ("Final", "Final Contribution Score (used for RAI allocation)")
    ]
    for abbrev, meaning in legend:
        print(f"{abbrev:<8} = {meaning}")
    print("═" * 90)

    # Print table
    print("\n" + "═" * 170)
    print(f"{'Rank':<5} {'Moniker':<50} {'Sub':<8} {'CrC':<8} {'EfC':<10} {'SR':<8} {'JC':<8} {'Eff':<10} {'Pres':<8} {'Steady':<8} {'Help':<8} {'Build':<8} {'Final':<10}")
    print("═" * 170)

    for r in results[:40]:
        print(f"{r['rank']:<5} {r['display_name']:<50} {r['jobs_submitted']:<8} {r['creation_completed']:<8} {r['effort_completed']:<10} {r['success_rate']:<8.4f} {r['job_creation_score']:<8.4f} {r['effort_score']:<10.4f} {r['presence_score']:<8.4f} {r['steadiness_score']:<8.4f} {r['helpfulness_score']:<8.4f} {r['builder_multiplier']:<8.4f} {r['final_score']:<10.4f}")

    if len(results) > 40:
        print(f"\n... and {len(results)-40:,} more miners")

    print("═" * 170)

    # RAI Distribution
    total_final = sum(r["final_score"] for r in results)
    if total_final == 0:
        total_final = 1

    for r in results:
        raw_rai = (r["final_score"] / total_final) * TOTAL_POOL
        r["estimated_rai"] = min(raw_rai, MAX_PER_WALLET)
        r["capped"] = "YES" if raw_rai > MAX_PER_WALLET else "NO"

    # Save full CSV
    with open("full_payout.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Meaning of the abbreviated words"])
        for abbrev, meaning in legend:
            writer.writerow([abbrev, meaning])
        writer.writerow([])
        writer.writerow(["Rank","Moniker","Jobs_Submitted","Creation_Completed","Effort_Completed","Success_Rate","Job_Creation_Score","Effort_Score","Presence_Score","Steadiness_Score","Helpfulness_Score","Builder_Multiplier","Final_Score","Estimated_RAI","Capped"])
        for r in results:
            writer.writerow([r["rank"], r["display_name"], r["jobs_submitted"], r["creation_completed"], r["effort_completed"], r["success_rate"], r["job_creation_score"], r["effort_score"], r["presence_score"], r["steadiness_score"], r["helpfulness_score"], r["builder_multiplier"], r["final_score"], round(r["estimated_rai"], 2), r["capped"]])

    # Save simple payment CSV
    with open("simple_payout.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["address","estimated_rai"])
        for r in results:
            writer.writerow([r["address"], round(r["estimated_rai"], 2)])

    print("\n✅ Full payout files generated:")
    print("   - full_payout.csv (detailed report with all scores)")
    print("   - simple_payout.csv (address + estimated_rai for payments)")
    print("   Legend is at the top of full_payout.csv")

def calculate_longest_streak(active_days):
    if not active_days:
        return 0
    sorted_days = sorted(datetime.fromisoformat(d) for d in active_days)
    longest = 1
    current = 1
    for i in range(1, len(sorted_days)):
        if (sorted_days[i] - sorted_days[i-1]).days == 1:
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    return longest

if __name__ == "__main__":
    main()