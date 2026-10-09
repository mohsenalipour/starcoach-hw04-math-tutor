# نمونه‌های اجرای واقعی

این فایل‌ها State ثبت‌شدهٔ اجراهای واقعی در ۹ اکتبر ۲۰۲۶ هستند. کلید API در آن‌ها وجود ندارد. نام نودها، تغییر State و نوع بررسی در هر فایل دیده می‌شود.

| فایل | مدل این اجرا | نتیجه |
|---|---|---|
| [problem-state.json](problem-state.json) | `gpt-4o-mini` | مشتق `x^3 + 2*x` برابر `3*x^2 + 2`؛ `answered` و `symbolically_checked` |
| [concept-state.json](concept-state.json) | `gpt-4.1-mini` | توضیح شهودی انتگرال با انباشت مسافت؛ `answered` و `model_reviewed` |
| [clarification-state.json](clarification-state.json) | `gpt-4o-mini` | درخواست بدون معادله؛ سؤال تکمیلی و `needs_clarification` |

متن این snapshotها همان خروجی ثبت‌شده است؛ نسخهٔ نهایی CLI شماره‌های اضافهٔ نوشته‌شده توسط مدل را هنگام نمایش مراحل حذف می‌کند. متن پاسخ مدل می‌تواند بین اجراها متفاوت باشد. برای بررسی اجرای فعلی از `--trace` و `--state-file` استفاده کنید.
