# LeadEngine — User Guide (simple English + Roman Urdu)

This guide takes you from a fresh PC to sending your first (approved) cold email.
Har step ke neeche Roman Urdu mein short explanation bhi hai.

---

## 1. One-time setup (sirf pehli dafa) — Windows, no typing needed

1. **Install Python**: go to python.org → Downloads → Python 3.12 → run it →
   **tick "Add Python to PATH"** at the bottom → Install Now.
2. **Download LeadEngine**: github.com/arslan55333/arslan553 → green **Code** button → **Download ZIP**.
   Right-click the ZIP → **Extract All** → choose `C:\LeadEngine`.
3. Open the `C:\LeadEngine\...` folder and **double-click `setup.bat`**. Wait 5–10 minutes.
   At the end it shows a check list (OK / tip / FIX).
4. **Every day**: double-click **`start.bat`** → the dashboard opens in your browser.
   Keep the black window open while you work; close it when you're done.
5. Weekly: double-click **`backup.bat`**.

> **Urdu:** Sirf 2 files yaad rakhni hain: pehli dafa `setup.bat`, roz `start.bat`.
> Agar Windows "Windows protected your PC" dikhaye to **More info → Run anyway** dabao (ye aap ki apni file hai).
> Kaali window mein koi error aaye to uska text copy kar ke Claude ko bhej do.

Manual way (Mac/Linux or if you prefer typing):
```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m playwright install chromium
cp .env.example .env               # Windows: copy .env.example .env
python -m leadengine doctor
python -m leadengine ui
```

**Updating to a new version later:** download the new ZIP into a *new* folder, then copy these from the
old folder into the new one: the **`data`** folder (all your leads), **`.env`** and **`config.toml`**.
Then run `setup.bat` once in the new folder.

### Keys (`.env` file, Notepad mein kholo)
Nothing is required to start. The scraper is free. Add keys only when you need them:

| Key | Why | Cost |
|---|---|---|
| `PAGESPEED_API_KEY` | mobile speed in Website Score | free (Google Cloud) |
| `ANTHROPIC_API_KEY` (or Gemini/Groq) | AI-written preview copy + email drafts | pay per use |
| `SERPAPI_API_KEY` | fills missing phone/website for top leads | 250 free/month |
| `NETLIFY_TOKEN` | publish preview sites online | free plan |
| `OUTREACH_SMTP_USER` / `OUTREACH_SMTP_PASSWORD` | save drafts to your mailbox / send | free |

> **Urdu:** Keys sirf `.env` mein likho, kabhi code ya config.toml mein nahi. `.env` GitHub par upload
> nahi hoti. AI key ke baghair bhi tool chalta hai: wo achay template use karta hai.

### Your details (`config.toml`)
Open `config.toml` and fill in:
- `[preview]` → `brand_name` (your agency name), `brand_url`, `brand_email`
- `[outreach]` → `sender_name`, `physical_address` (**required by US law before emailing**), `offer`

---

## 2. Find leads (leads dhoondna)

**One ZIP:**
```bash
python -m leadengine discover "septic service" --zip 75201
```
**A whole city or many ZIPs (recommended):**
```bash
python -m leadengine scan "septic service" --city "Dallas, TX"
python -m leadengine scan "roofing" --near 75201 --radius 20
python -m leadengine scan "plumber" --zip 75201,75204,75206
```

What happens automatically: free Google Maps scan of the whole ZIP → saves every business → for the good
ones (20+ reviews, 4.0+ stars): emails, Website Score, Google Ads check → Hot / Warm / Cold labels.

> **Urdu:** `scan` beech mein band ho jaye (Ctrl+C, light chali gayi, captcha) to koi masla nahi.
> Jitna kaam ho chuka hai wo save hai. Dobara chalao: `python -m leadengine scan --resume 1`
> (job number `python -m leadengine jobs` se milega). Same search dobara chalao to cache se aata hai, free.

Which ZIPs are worth it? `python -m leadengine zips --state TX --keyword "septic service"` ranks ZIPs by
population/income and marks what you already scanned.

---

## 2b. Your best leads: Ads finder (sab se behtar tareeqa)

Your target is businesses that **pay for Google Ads but have a weak website**. The fastest way to find them:

1. Dashboard → **Ads finder**.
2. Keyword: e.g. `dumpster rental`. Places: one per line, e.g. `New York, NY` and `Jersey City, NJ`.
3. Click **Find advertisers** and wait. It does many Google searches with pauses, so it takes a few minutes.
4. The result list shows:
   - every business paying Google: search ads, Local Services Ads and map ads;
   - how often they showed up;
   - their **landing page score**: the page their ad sends people to. A low score is an easy sell;
   - website score and label.

> **Urdu:** Ye tool khud Google par "dumpster rental", "dumpster rental near me", "emergency …" jaisi
> kai searches har area se karta hai. Jo bhi ad chala raha hai (paisa de raha hai) wo list mein aata hai.
> Phir dekhta hai ke ad jis page par bhejta hai wo kitna kharab hai: call button nahi, form nahi,
> phone par theek nahi khulta, slow hai. Yahi aap ke sab se ache clients hain.
> Agar Google captcha dikhaye to tool ruk jata hai aur "check failed" likhta hai, "no ads" nahi.

## 2c. Rank map: Google Maps par kahan dikhta hai

On a lead page click **Rank map around this business**, or go to **Rank map** and enter a keyword + ZIP.

The tool searches Google Maps from 49 spots (7×7) around the area and draws a map:
- green = the business is in the top 3 there;
- red = it is not in the top 20.

You also get a list of **who owns the area** (share of top-3 spots).

> **Urdu:** Ye wahi cheez hai jiske liye log Local Falcon jaise tools ko har mahine paise dete hain.
> Client ko ye map dikhao: "aap sirf 5 jagah top 3 mein ho, competitor 40 jagah". Ye bohat convincing hota hai.

## 3. Look at the leads (dashboard)

```bash
python -m leadengine ui
```
Your browser opens **http://127.0.0.1:8765**.

- **Leads**: filter by Hot/Warm, "Ads: Active", "max site score", "verified email", or search a name.
- Open a lead to see:
  - why it's Hot;
  - website problems, with desktop and phone screenshots;
  - ads proof;
  - emails, with a confidence score.
- **Export CSV / Excel** buttons give you a spreadsheet of the current filter.

> **Urdu:** Hot = sab se acha mauqa: achay reviews, kharab website, aur aksar Google Ads par paisa bhi
> laga rahe hain. Pehle Hot leads par kaam karo.

The labels mean:

| Label | Meaning (simple) |
|---|---|
| Hot | good business + weak/old website (often already paying for ads) → contact first |
| Warm | worth contacting |
| Cold | low chance |
| Skip | closed, modern site already, or already contacted |

---

**Not checked?** In the leads list, "not checked" means the tool hasn't looked yet. It does **not** mean "no".
To check those leads:
- tick the leads and click **Deep-check selected**, or
- open a lead and click **Check this lead**.

Either way you get emails, website score, Google Ads and local SEO.

**Clickable dashboard:** the cards (Hot leads, Running Google Ads, With email) and the pipeline boxes open the
matching leads.

## 3b. Audit report (client ko bhejne wali report)

On a lead page click **Build audit report**. You get one good-looking page with:
- scores;
- the biggest problems;
- the map heatmap;
- the ads and landing-page problems;
- website screenshots;
- local SEO fixes;
- competitors;
- your preview concept.

Tick **Publish** to get an online link (needs `NETLIFY_TOKEN`). The email drafts will then include it.
To make a PDF: open the report → Ctrl+P → "Save as PDF".

> **Urdu:** Ye report email mein link ki tarah bhejo ya PDF bana kar attach karo. Isme sirf asli,
> measure ki hui cheezen hain. Koi jhoota claim nahi.

## 4. Build a preview website for a lead

On the lead page click **Build preview** (or `python -m leadengine preview --label hot --limit 10`).

You get a modern one-page site made for that business, with a desktop and phone screenshot.

- It is clearly marked as a *concept* and is hidden from Google.
- Tick **Publish** (needs `NETLIFY_TOKEN`) to get a link you can put in the email.

> **Urdu:** Ye business ki asli website nahi. Upar banner likha hota hai ke ye aap ki agency ka
> "preview concept" hai, taake koi dhoka na ho.

---

## 5. Email drafts (sirf drafts, kuch send nahi hota)

On the lead page click **Write email drafts**, or run:
```bash
python -m leadengine draft --label hot --show
```
For each lead you get:

- 3 versions of the first email:
  - **short**
  - **detailed**: their real website problems, plus the ads angle
  - **competitor**: a stronger competitor in their area
- 3 follow-ups (day 3, 7 and 14)

Every fact in the email comes from what the tool measured. Nothing is made up.

Use them in your email tool:
```bash
python -m leadengine outreach export drafts.csv         # import into Instantly / Smartlead / lemlist
python -m leadengine outreach export drafts-eml/        # .eml files: double-click opens a draft
python -m leadengine outreach push --imap               # straight into your Gmail/Outlook Drafts folder
```

> **Urdu:** Ye sirf drafts hain. Aap khud parho, edit karo aur apne email tool se bhejo.
> Har email ke neeche aap ka address aur "unsubscribe" line khud lag jati hai (US law CAN-SPAM).

When you send one yourself, mark the lead **Emailed** on its page (CRM).

The tool will then refuse to contact the same business twice, even under a different listing.

---

## 6. (Optional) Let LeadEngine send — only if you want

Sending is **off** by default. To turn it on:

1. Buy a separate domain for outreach, e.g. `getacme-web.com`. Do not use your main domain.
   Set up SPF/DKIM/DMARC and warm it up slowly.
2. Fill these in `config.toml`:
   - `[outreach]`: `sender_email`, `physical_address`
   - `[outreach.sending]`: `enabled = true`, `smtp_host`, and `allowed_from_domains = ["getacme-web.com"]`
3. Put the mailbox login (an app password) in `.env`.
4. On a lead page, click **Approve this one** on the draft you like.
5. Go to **Outbox**, tick the confirm box, and click **Send**.

The tool then sends **only the emails you approved**, with these limits:

- At most `max_per_day` per day (default 30).
- A pause of `min_delay_seconds` or more between emails.

Follow-ups go out by themselves on day 3, 7 and 14, but **stop as soon as the lead replies**. Run this to check replies:
```bash
python -m leadengine outreach replies
```
- A reply marks the lead as Replied.
- "Unsubscribe" adds them to the do-not-contact list.
- A bounced email is marked invalid.

> **Urdu:** Bhejna aap ki marzi hai. Tool sirf wahi email bhejta hai jo aap ne khud "Approve" ki ho,
> din mein limit ke andar, aur jo banda "unsubscribe" likhe usay dobara kabhi email nahi jati.

---

## 6b. Weekly watch: naye advertisers ka alert

Dashboard → **Alerts** → enter a keyword and places → **Save watch**.

Every week the tool runs the Ads finder again, and every business that **started** advertising becomes an
alert. You see a red number next to "Alerts" and a yellow box on the dashboard.

- It runs by itself while `start.bat` is open.
- To run it even when the dashboard is closed, double-click **`schedule.bat`** once. It checks every
  morning at 9.
- Optional: put a webhook URL in `.env` as `ALERT_WEBHOOK_URL` (n8n / Make / Zapier → WhatsApp, Slack or
  email) to get alerts on your phone.

> **Urdu:** Jo business is hafte naya naya ads chalana shuru karta hai, wo abhi abhi paisa kharch karne
> ko tayyar hai. Ye sab se garam (hot) khareedar hote hain. Inhein sab se pehle contact karo.

## 7. Keep it healthy (maintenance)

| Command | What it does |
|---|---|
| `python -m leadengine backup` | copy of your database in `data/backups/` (do this weekly) |
| `python -m leadengine prune` | removes old cache versions, makes the file smaller |
| `python -m leadengine credits` | how many paid API calls you used this month |
| `python -m leadengine doctor` | if something stops working, run this first |

> **Urdu:** Hafte mein ek dafa `backup` chala lo. Koi masla ho to pehle `doctor` chalao. Ye batata hai
> kya missing hai.

---

## Common problems (aam masail)

| Problem | Fix |
|---|---|
| "Executable doesn't exist" / browser error | `python -m playwright install chromium` |
| Google shows a captcha, scan stops | wait a bit; for big runs add residential proxies (`PROXIES=` in `.env`), then `scan --resume <id>` |
| No emails found for a site | many small businesses only have a contact form; the phone number is still in the lead |
| Drafts say "[ADD YOUR MAILING ADDRESS…]" | fill `physical_address` in `[outreach]`, then click "Regenerate drafts" |
| Preview has no link in the email | publish it (`NETLIFY_TOKEN` + "Publish"); otherwise the screenshot is attached |
| Want to start over for one ZIP | add `--refresh` to `discover` / `scan` |
| Leads show "not checked" | tick them → **Deep-check selected** (big cities: Google shows businesses from the whole area) |
| Ads say "check failed" | Google showed a captcha or the internet dropped — wait, then **Check this lead** again |
| Rank map has no map background | the map pictures come from OpenStreetMap over the internet; the dots are still correct |
