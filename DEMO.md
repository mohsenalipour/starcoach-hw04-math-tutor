# راهنمای دمو و تست تمرین چهارم

برای دمو، سه رفتار اصلی را نشان بده: آموزش مفهوم، حل مرحله‌به‌مرحله با بررسی مستقل و پرسیدن سؤال تکمیلی وقتی اطلاعات کافی نیست. سپس تغییر سطح و State را نمایش بده. همهٔ ورودی‌ها از CLI وارد می‌شوند.

## آماده‌سازی

روی سیستم محسن، محیط مجازی و تنظیمات مدل آماده‌اند. در PowerShell:

```powershell
cd 'D:\StarCoach\01-Agentic AI Bootcamp\01-HW\04\MATH_TUTOR'
.\run.ps1 --language en --level beginner --trace
```

روی سیستم تازه، ابتدا مراحل نصب در README و تنظیم `.env` لازم است. برای اجرای مدل اینترنت و اعتبار API نیاز داری؛ تست‌های خودکار بدون آن‌ها اجرا می‌شوند.

اگر اجرای `run.ps1` با خطای Execution Policy متوقف شد، همان برنامه را مستقیماً اجرا کن:

```powershell
.\.venv\Scripts\python.exe cli.py --language en --level beginner --trace
```

## سناریوی دمو داخل ترمینال

این ورودی‌ها را یکی‌یکی وارد کن و هر بار منتظر پاسخ بمان:

| ترتیب | ورودی | چیزی که توضیح می‌دهی |
|---|---|---|
| ۱ | `Explain derivatives to a beginner using an everyday example.` | مسیر مفهومی، مثال ساده و نود `explain_concept` |
| ۲ | `Differentiate x^3 + 2*x with respect to x, step by step.` | مسیر حل، نود `solve_problem` و بررسی `calculate`؛ نتیجهٔ درست `3*x**2 + 2` است |
| ۳ | `/trace` | نودهای آخرین اجرا و `symbolically_checked` برای این مشتق |
| ۴ | `/state` | `analysis.request_kind`، `learner_level`، `plan`، `tool_result`، `review` و `trace` |
| ۵ | `/new` | شروع گفت‌وگوی تازه برای نمونهٔ مبهم |
| ۶ | `Solve this equation.` | ایجنت باید خودِ معادله را درخواست کند؛ مسیر `ask_clarification` |
| ۷ | `2*x + 3 = 7` | استفاده از سؤال قبلی و پاسخ `x = 2` |
| ۸ | `/level advanced` | تغییر سطح به پیشرفته |
| ۹ | `Explain the power rule using the definition of the derivative.` | توضیح فنی‌تر متناسب با سطح جدید |
| ۱۰ | `/exit` | خروج |

شروع و پایان هر نود با `--trace` دیده می‌شود. پاسخ‌ها زنده از مدل می‌آیند، بنابراین جمله‌بندی و زمان پاسخ ثابت نیست. در صورت ورود به `revise_answer` توضیح بده که بازبینی اولیه ایرادی پیدا کرده و اصلاح محدود انجام می‌شود. وضعیت `unverified` یعنی پاسخ نهایی تأیید نشده است؛ آن را به عنوان حل موفق ارائه نکن.

برای بررسی کوتاه و مستقل یک مسئله، این دستور هم کافی است:

```powershell
.\run.ps1 -q 'Differentiate x^3 + 2*x with respect to x, step by step.' --language en --level beginner --trace
```

## فارسی بدون تایپ در PowerShell

ترمینال ممکن است متن فارسی را وارونه یا حروف را جدا نمایش دهد. این مشکل به پشتیبانی راست‌به‌چپ و اتصال حروف مربوط است؛ UTF-8 به‌تنهایی آن را حل نمی‌کند. [گزارش رسمی Windows Terminal](https://github.com/microsoft/terminal/issues/538)

برای دمو کاملاً داخل ترمینال از زبان انگلیسی استفاده کن. برای بررسی فارسی، سؤال را از فایل UTF-8 بخوان و پاسخ را در گزارش HTML راست‌به‌چپ ببین. فایل‌های `demo/*-fa.txt` سؤال آماده دارند؛ پاسخ ذخیره‌شده یا از پیش تعیین‌شده ندارند.

```powershell
.\run.ps1 --input-file .\demo\problem-fa.txt --language fa --level beginner --trace --html .\demo\output\problem-fa.html --state-file .\demo\output\problem-fa.json
Start-Process .\demo\output\problem-fa.html
```

گزارش شامل سؤال، پاسخ، مسیر اجرا و State است. مرورگر فقط گزارش را نمایش می‌دهد؛ پردازش و تعامل با ایجنت همچنان از CLI انجام می‌شود.

برای سؤال خودت، فایل را در Notepad یا VS Code ویرایش و با Encoding برابر UTF-8 ذخیره کن:

```powershell
notepad .\demo\question-fa.txt
.\run.ps1 --input-file .\demo\question-fa.txt --language fa --level beginner --html .\demo\output\answer-fa.html
Start-Process .\demo\output\answer-fa.html
```

فایل جدید را ابتدا ذخیره کن، سپس دستور اجرا را بزن. اگر فایل موجود نباشد یا UTF-8 نباشد، برنامه خطای قابل‌خواندن می‌دهد.

## گفت‌وگوی چندنوبتی فارسی با فایل

ابتدا برنامه را اجرا کن:

```powershell
.\run.ps1 --language fa --level beginner --trace --html .\demo\output\conversation-fa.html
```

سپس داخل برنامه این فرمان‌های انگلیسی را یکی‌یکی وارد کن:

```text
/file demo/concept-fa.txt
/file demo/problem-fa.txt
/new
/file demo/clarification-fa.txt
/file demo/equation-fa.txt
/trace
/html demo/output/last-answer.html
/exit
```

گزینهٔ `--html` بعد از هر پاسخ فایل گزارش را با آخرین نتیجه به‌روز می‌کند. فایل HTML را یک بار در مرورگر باز کن و بعد از هر پاسخ Refresh بزن. `/html` نیز آخرین نتیجه را در مسیر دلخواه ذخیره می‌کند. فایل گزارش خارج از حالت گفت‌وگو باز می‌شود؛ می‌توان آن را از File Explorer باز کرد.

## اگر متن به صورت علامت سؤال یا نویسه‌های خراب دیده می‌شود

برنامه ورودی و خروجی Python را از ابتدا UTF-8 می‌کند. اگر خود PowerShell یا ابزارهای دیگر نویسه‌ها را خراب می‌کنند، برای همان پنجره تنظیم زیر را انجام بده:

```powershell
chcp 65001 > $null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
```

این تنظیم encoding را هماهنگ می‌کند؛ ترتیب راست‌به‌چپ و اتصال حروف همچنان به ترمینال وابسته است. برای فایل ورودی به تبدیل encoding یا تغییر تنظیمات عمومی Windows نیاز نیست.

## تست خودکار محلی

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

موفقیت تست‌ها با `OK` در پایان مشخص می‌شود. این تست‌ها رفتار برنامه را با مدل شبیه‌سازی‌شده بررسی می‌کنند؛ برای دمو، سناریوهای بالا را با مدل واقعی اجرا کن.
