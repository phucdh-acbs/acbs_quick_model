# Thiết kế L1 ngân hàng

Trạng thái: **owner duyệt có sửa (2026-11-17), đã build bản v0.1** (`config/templates/bank`, `quick_model/families/bank`).
Bảng dưới là quyết định cuối và **thay thế** các đề xuất ở mục 3–8 nếu khác nhau.

## 0. Quyết định của owner (2026-11-17)

| Phần | Cách tính đã duyệt | Đã build |
|---|---|---|
| Cho vay | Dư nợ = năm trước × (1 + EMA5 tốc độ tăng trưởng dư nợ), một mức cho mọi năm | `loan_growth` = EMA span 5 năm của YoY BSB104 |
| Huy động | Tiền gửi KH = năm trước × (1 + EMA5 tăng trưởng tiền gửi). **LDR chỉ là trần**, không dùng để tính huy động. LDR = dư nợ gộp / **tổng huy động** (sửa 2026-11-17) | Tổng huy động = tiền gửi KH + vốn tài trợ, ủy thác + GTCG (BSB113 + BSB115 + BSB116); liên NH và vay NHNN không tính (liên NH là dòng cân). `max_ldr` = cao nhất 3 năm gần nhất; chỉ khi vượt trần thì tiền gửi được nâng lên để tổng huy động = dư nợ / trần (công bố BANK-LIQ-01) |
| GTCG | EMA5 tỷ lệ GTCG / tiền gửi KH × tiền gửi KH | `valuable_papers_ratio` |
| Dự trữ | Mặc định 3% | `sbv_reserve_ratio` = 3% × (tiền gửi KH + GTCG), nguồn chính sách |
| Chứng khoán đầu tư | Tỷ lệ kỳ liền trước (sửa 2026-11-17, thay EMA5) | `inv_securities_to_loans` = CK đầu tư / dư nợ của kỳ gốc |
| Chi phí dự phòng | EMA5 chi phí tín dụng × dư nợ (sửa 2026-11-17: dư nợ cuối kỳ, thay dư nợ bình quân) | `credit_cost` = −ISB41 / BSB104 cuối kỳ, cùng định nghĩa ở tab Tỷ lệ chính |
| Quỹ dự phòng | EMA5 tỷ lệ bao phủ × dư nợ | `llr_coverage`; xoá nợ ngụ ý là phần dư, công bố |
| Thu nhập lãi thuần | Lợi suất × TS sinh lãi BQ − chi phí vốn × nợ chịu lãi BQ; lợi suất và chi phí vốn = **kỳ liền trước** (sửa 2026-11-17, thay EMA5 vì EMA hai chỉ số lệch chu kỳ làm chênh lệch lãi nở ~0,55 điểm %) | `yield_iea`, `cost_of_funds` (stat `latest`); NIM tính ngược ở tab Tỷ lệ chính; NIM = lợi suất − chi phí vốn × (nợ chịu lãi BQ / TS sinh lãi BQ) |
| Phí dịch vụ | EMA5 tỷ lệ phí × dư nợ BQ | `fee_to_loans` |
| Thu nhập khác | Năm trước × (1 + g), g = EMA5 tăng trưởng cuốn chiếu (sửa 2026-11-17): mỗi năm dự phóng tính lại EMA trên cửa sổ 10 quan sát gồm cả g đã dự phóng | `other_income_growth` trên tổng ngoại hối + CK kinh doanh + CK đầu tư + hoạt động khác + góp vốn |
| Chi phí hoạt động | EMA5 CIR × TOI | `cir` |
| Sửa 2026-11-18 | Dư nợ: năm 1 = EMA5, giảm tuyến tính về 15% ở năm 3 rồi giữ; tiền gửi KH: tương tự về 13%; CK đầu tư = năm trước × (1 + EMA5 YoY); block dự phòng đổi tên "Chi phí tín dụng", bỏ tỷ lệ bao phủ, dự phòng cho vay giữ nguyên số kỳ gốc | `ema_decay` (target, target_year); `inv_securities_growth` |
| Sửa 2026-11-18 (2) | Khoản bất thường: quý bị cờ (trend OLS 12 quý, 3σ, ≥ 2% TOI) được **thay bằng trung bình 12 quý trong cửa sổ** (k = thực tế − trung bình), cộng lại thành năm → tăng trưởng điều chỉnh → EMA5 cuốn chiếu; sau khi dự phóng, k của các quý thuộc năm dự phóng được **cộng ngược lại** vào số cả năm (tự áp, analyst bỏ qua được). Phí dịch vụ / dư nợ BQ = EMA5 cuốn chiếu | `one_off.replace_with = mean`, `auto_current = true`; `fee_to_loans` stat `ema_rolling`. TCB: 21 khoản tự áp; g điều chỉnh 12,7% → 12,0%; phí/dư nợ 1,59% → 1,57% |
| **Duyệt 2026-11-18** | Owner duyệt thiết kế Bank L1 theo năm (bản sau các sửa ngày 17–18/11) | — |
| Xem theo quý (2026-11-18, phương án A) | Bỏ engine quý riêng (EMA20 quý đã thử trong ngày rồi thay). Chỉ engine năm; xem theo quý = phân bổ số năm: dòng luồng = (cả năm − lũy kế đã công bố) × trọng số mùa vụ của TOI (EMA 5 năm), số dư đi tới số dư cuối năm theo trọng số lũy kế, Q4 = số năm; tỷ lệ quý trên dashboard quy năm 365 / số ngày quý, tăng trưởng so cùng quý năm trước; cảnh báo PHASE-01 khi quý còn lại trái dấu. Horizon quý: đến hết Q4 năm sau | `quick_model/phasing.py`, `family.phasing_key = "toi"`, `frequencies.implemented = [annual]` |
| Engine quý + năm (2026-11-18, phương án B, thay phương án A) | Phân bổ không bắt được đà ngắn hạn (TCB: AEY các quý 2026 tăng trong khi xu hướng năm giảm). Vòng tính theo quý từ sau quý công bố mới nhất đến hết Q4 năm sau (số dư đầu = BS quý đó, tăng trưởng so cùng quý năm trước, lãi trên bình quân 2 quý, ngày thực tế), rồi theo năm; năm có quý = tổng quý. Default quý = 0,65 × EMA span 8 quý liên tiếp (cuốn chiếu, chuỗi đã làm sạch) + 0,35 × default năm (năm giữ nguyên, kể cả decay 15%/13%). λ = 0: chi phí tín dụng, tăng trưởng thu nhập ngoài lãi khác, thuế, cổ tức; trần LDR, sàn/trần liên NH, dự trữ NHNN chỉ áp cuối năm (Q4); quý 1–3 liên NH (TS) chỉ giữ không âm. Analyst ghi đè số năm = mục tiêu năm (lợi suất, chi phí vốn, phí, chi phí tín dụng, CIR, thuế, tăng trưởng thu nhập khác giải cho quý còn lại; tăng trưởng số dư khớp ở Q4). Quý đã công bố giữ số thực tế (gồm khoản bất thường) trong tổng năm; bỏ PHASE-01 | `quick_model/hybrid.py`, `quarterly_engine` trong template |
| AEY, COF chỉ theo quý (2026-11-18) | NIM/AEY/COF phụ thuộc mức gần: lợi suất TS sinh lãi và chi phí vốn quý = EMA 8 quý cuốn chiếu, không gộp giá trị năm (λ = 1, `quarter_only`); các năm sau phần quý giữ mức quý cuối. Chuỗi chưa đủ cho EMA (năm < 5, quý < 8) dùng số kỳ liền trước (`short_history`), decay 15%/13% giữ nguyên | `quarterly_engine.quarter_only`, `default_paths.short_history` |
| Chốt (2026-11-18) | Owner chốt bản engine quý + năm (L1 phi tài chính v2.2, ngân hàng dùng chung engine; AEY/COF theo quý) | — |
| Thu nhập ngoài lãi khác (2026-11-19, cùng L1 phi tài chính v2.5) | Backtest: quy tắc làm sạch tuần tự trên 5 dòng con cắt 443 quý (86% là khoản dương, khoảng 32% tổng thu nhập ngoài lãi khác 2017–2025) nên dự phóng thấp có hệ thống (−40% đến −57%). Owner duyệt: **không tự làm sạch** (vẫn phát hiện và công bố, analyst xác nhận từng khoản: `auto_history`, `auto_current` = false); thu nhập ngoài lãi khác = **mức theo năm × ngày / 365**; default năm = bình quân 2 năm gần nhất; quý = bình quân 8 quý gần nhất của số quy năm (λ = 1), các năm sau giữ mức quý cuối. Thay `other_income_growth` bằng `other_income_run_rate`. Chi phí tín dụng giữ nguyên (đã thử theo NPL, nợ xấu phát sinh, mùa vụ: không thắng ổn định); chỉ report lãi thuần, chưa tách AEY/COF để sửa | `config/templates/bank/template.json`, `NonInterestIncome` |
| Yêu cầu 1 | Bộ tính chỉ số ngân hàng; NIM tính ngược để so lịch sử | `quick_model/families/bank/metrics.py` (default) và `ratios.py` (cùng công thức cho cột A và F) |
| Yêu cầu 2 | Lọc khoản bất thường đi từ quý: tìm quý outlier, loại, cộng lại thành năm rồi mới tính EMA5 (sửa 2026-11-17) | Quét tuần tự 9 năm lịch sử (`history_years`) từ cũ đến mới, quý bị cờ được thay bằng trend trong chuỗi quét năm sau; khoản ở năm lịch sử **tự loại** (`auto_history`, ONE-03, analyst bỏ qua được); khoản ở quý đã công bố của năm dự phóng vẫn cần analyst xác nhận (ONE-01). Giới hạn: quét tuần tự có thể "khóa" trend cũ khi dòng dịch chuyển mức kéo dài (ví dụ ngoại hối TCB từ 2023Q3) |

Các điểm không bị sửa giữ như đề xuất: TS Có khác / nợ khác theo tỷ lệ (EMA5); thuế, cổ đông không kiểm soát theo
trung vị; cổ tức và phát hành mặc định 0; cân thanh khoản qua liên ngân hàng tự động thay biến cân tiền mặt; chỉ
chạy theo năm.
Căn cứ:
- model card 9 ngân hàng (`output_from_analyst/model_cards_hungcv_20261111/output/bank/`, gồm TCB);
- dữ liệu `data/raw/tcb/` (BS, IS, thuyết minh; không có LCTT);
- mapping `mapping_fiin_all.xlsx`, nguồn `*-hop-nhat-nhom-ngan-hang`;
- khung OOP hiện có của nhóm phi tài chính.

## 1. Dữ liệu TCB đã kiểm tra

| Nội dung | Kết quả |
|---|---|
| Kỳ | BS và IS: báo cáo năm 2016–2025 (10 năm) cùng các quý đến 2026 (Q1–Q3, 6T, 9T). Thuyết minh: **chỉ từ 2023** |
| Đẳng thức | Tổng TS = tổng nguồn vốn; Σ dòng TS cấp 1 = BSA53; Σ nợ cấp 1 = BSA54; TOI = Σ thành phần (ISB38); LNTT = TOI + chi phí HĐ + dự phòng: **khớp tuyệt đối mọi năm** |
| Mã | BS dùng BSA (chung) + BSB (riêng NH); IS dùng ISB25–41 + ISA16–23; thuyết minh NOB1–219 (cột viết kiểu CamelCase, khác BS/IS) |
| Thiếu | Không có LCTT (đúng chủ trương: không cần CF). CAR trong thuyết minh (NOB151) = 0, không dùng được |

Hệ quả: **L1 chỉ dựa vào BS + IS** (đủ 10 năm). Thuyết minh (nợ xấu theo nhóm, CASA, lãi theo nguồn) chỉ có 3 năm.
Chúng dùng để hiển thị tham chiếu và để dành cho L2/L3, không làm default L1.

## 2. Nguyên tắc

1. **Cân BS bằng IS, không có CF.** VCSH cuối kỳ = đầu kỳ + LNST mẹ − cổ tức + phát hành. Mọi dòng BS có method
   riêng. Tổng TS = tổng nguồn vốn nhờ **một dòng cân thanh khoản được khai báo** (mục 5), không dùng biến cân ẩn.
2. **Không chép cách cân của analyst.** Cả 9 card dùng *tiền mặt* làm biến cân. Theo CLAUDE.md, cách này được thay
   bằng tiền gửi/vay liên ngân hàng tự động, có sàn, có trần, công bố số tiền.
3. Hệ số gõ trong công thức của card (dự trữ NHNN 3%, dự phòng chung 0,75%, lợi suất TP TCTD 6%, …) thành giả
   định có tên.
4. **Tái dùng tối đa khung phi tài chính:**
   - đường default hội tụ / EMA / mùa vụ;
   - cấu trúc cố định + biến đổi (đổi doanh thu thành TOI);
   - hợp đồng giả định, template kế thừa;
   - giải thích ô, dải kịch bản, khoản bất thường, hồ sơ mô hình, dashboard.
5. L1 phi tài chính đã chốt: mọi refactor phải giữ nguyên `test_l1_frozen` và 285 test hiện có.

## 3. Block và method L1 (một method mỗi block)

Ký hiệu: BQ = bình quân đầu kỳ và cuối kỳ; "hội tụ" = như phi tài chính (từ thực tế gần nhất về EMA 5 năm trong 4 năm).

| Block | Method L1 | Công thức | Card tương ứng |
|---|---|---|---|
| Cho vay (`loans`) | `loans.L1.growth` | Dư nợ KH gộp (BSB104) = năm trước × (1 + g) | 9/9 card: tăng trưởng tín dụng |
| Huy động (`funding`) | `funding.L1.ldr` | TG KH (BSB113) = dư nợ gộp / LDR. GTCG (BSB116) = tỷ lệ GTCG/TG KH × TG KH | Card: tăng trưởng TG KH; GTCG theo TG KH |
| Dự trữ & thanh khoản (`liquidity`) | `liquidity.L1.ratios` | TG NHNN (BSB97) = tỷ lệ × (TG KH + GTCG); tiền mặt (BSA2) = tỷ lệ × TG KH | Card: TG NHNN = 2–3% × (TG KH + GTCG), hệ số gõ tay |
| Chứng khoán đầu tư (`securities`) | `securities.L1.to_loans` | CK đầu tư gộp (BSB107 + BSB108) = tỷ lệ/dư nợ × dư nợ | Card: TP đi theo tốc độ tín dụng |
| Chất lượng tài sản (`asset_quality`) | `asset_quality.L1.credit_cost` | Chi phí dự phòng (ISB41) = −chi phí tín dụng × dư nợ BQ. Dự phòng cho vay (BSB105) = −tỷ lệ bao phủ × dư nợ. **Xoá nợ ngụ ý** = dự phòng đầu kỳ + trích lập − dự phòng cuối kỳ (công bố) | 9/9 card: chi phí tín dụng × dư nợ BQ |
| Thu nhập lãi thuần (`nii`) | `nii.L1.yield_cof` | Thu nhập lãi = lợi suất × TS sinh lãi BQ; chi phí lãi = −chi phí vốn × nợ chịu lãi BQ | Card L3 (theo lớp TS / nguồn); L1 là bản gộp của chính công thức đó |
| Ngoài lãi (`non_interest`) | `non_interest.L1.ratios` | Lãi thuần dịch vụ (ISB30) = tỷ lệ phí × dư nợ BQ; thu nhập ngoài lãi khác (ISB31 + 32 + 33 + 36 + 37) = tỷ lệ × TS sinh lãi BQ | Card: tăng trưởng phí, ngoại hối nhập tay |
| Chi phí hoạt động (`opex`) | `opex.L1.cost_structure` | Chi phí HĐ (ISB39) = −(cố định + tỷ lệ biến đổi × TOI). **Dùng lại class/default cố định + biến đổi** của CPBH/CPQL | Card: tăng trưởng chi phí nhân viên |
| Thuế & vốn chủ (`tax_equity`) | `tax_equity.L1.policies` (dùng lại) | Thuế = −thuế suất × LNTT nếu LNTT > 0; CĐTS = tỷ lệ × LNST; cổ tức tiền = tỷ lệ × LNST mẹ; VCSH mẹ, LICĐKKS lăn số | 9/9 card |
| Cân thanh khoản (`balancing`) | `balancing.L1.interbank` | Mục 5 | Thay biến cân "tiền mặt" của card |

**Giữ nguyên số dư kỳ gốc (công bố là giản lược):**
- chứng khoán kinh doanh, phái sinh (TS và nợ), mua nợ;
- góp vốn dài hạn, TSCĐ, BĐS đầu tư;
- nợ Chính phủ và NHNN (BSB111; card TCB coi là nhập tay), vốn tài trợ uỷ thác.

**Theo tỷ lệ, hội tụ:**
- TS Có khác (BSB110) / dư nợ (card: tăng theo tín dụng);
- nợ khác (BSB117) / TG KH (card: tăng theo TG KH).

TS sinh lãi = TG NHNN + liên NH (TS) + CK kinh doanh + cho vay gộp + mua nợ + CK đầu tư gộp.
Nợ chịu lãi = nợ CP/NHNN + liên NH (nợ) + TG KH + vốn uỷ thác + GTCG.

**Không vòng lặp:** các dòng có method riêng không phụ thuộc lợi nhuận, nên số dư BQ của chúng tính thẳng. Riêng
phần tự động của dòng cân thanh khoản (mục 5) chỉ tính lãi trên số dư đầu kỳ, như cách phi tài chính tính lãi vay
đầu kỳ, và điều này được công bố.

## 4. Giả định L1 và default

| Giả định | ID | Default đề xuất | Tham chiếu TCB 2016–2025 (thực tế) |
|---|---|---|---|
| Tăng trưởng dư nợ | `loan_growth` | Hội tụ: từ YoY gần nhất về `long_run_loan_growth`, đạt ở năm thứ 5 | 2019–2025: 20–44%/năm |
| Tăng trưởng dư nợ dài hạn | `long_run_loan_growth` | min(EMA 5 năm YoY, **trần cần duyệt**, đề xuất 12%) | — |
| LDR (dư nợ gộp / TG KH) | `ldr` | Hội tụ | 0,82 → 1,24 |
| GTCG / TG KH | `gtcg_to_deposits` | Hội tụ | 6% → 35% (tăng mạnh) |
| TG NHNN / (TG KH + GTCG) | `sbv_reserve_ratio` | Hội tụ | Biến động 1,4–13% |
| Tiền mặt / TG KH | `cash_to_deposits` | Hội tụ | ~0,5% |
| CK đầu tư / dư nợ | `inv_securities_to_loans` | Hội tụ | Giảm dần |
| TS Có khác / dư nợ; nợ khác / TG KH | `other_assets_to_loans`, `other_liab_to_deposits` | Hội tụ | — |
| Chi phí tín dụng / dư nợ BQ | `credit_cost` | Hội tụ (EMA giảm ảnh hưởng năm 2017 = 2,4%) | 0,5–1,0% từ 2019 |
| Dự phòng / dư nợ gộp | `llr_to_loans` | Hội tụ | 0,8–1,5% |
| Lợi suất TS sinh lãi | `yield_iea` | Hội tụ | 6,7–8,2% |
| Chi phí vốn | `cost_of_funds` | Hội tụ | 2,2–4,6% |
| Lãi thuần dịch vụ / dư nợ BQ | `fee_to_loans` | Hội tụ | — |
| Ngoài lãi khác / TS sinh lãi BQ | `other_income_to_iea` | Trung vị 3 năm (dòng biến động, không hội tụ). Có phát hiện khoản bất thường | 5–22% TOI |
| Chi phí HĐ cố định / tỷ lệ biến đổi theo TOI | `opex_fixed`, `opex_variable_ratio` | Hồi quy Δchi phí theo ΔTOI (như CPBH/CPQL); lỗi thì CIR trung vị | CIR 29–35% |
| Thuế suất hiệu dụng | `effective_tax_rate` | Trung vị (dùng lại) | ~20–21% |
| Tỷ lệ LNST CĐTS | `minority_share` | Trung vị (dùng lại) | 0–2,6% |
| Cổ tức tiền / LNST mẹ; phát hành | `cash_dividend_payout`, `share_issue_proceeds` | Chính sách 0, analyst khai (dùng lại) | — |
| Sàn TG & cho vay liên NH / TG KH | `min_interbank_assets_ratio` | Thấp nhất lịch sử | — |
| Trần nợ liên NH / tổng TS | `max_interbank_liab_to_assets` | Cao nhất lịch sử | — |

Quy tắc chung giữ như phi tài chính:
- nguồn giá trị (`historical_default`, `analyst`, `explicit_policy`, `auto_balance`, `needs_input`); để trống khác 0;
- tỷ lệ là theo năm; lịch sử đã loại khoản bất thường đã xác nhận trước khi tính default.

## 5. Cân thanh khoản tự động (thay biến cân "tiền mặt")

Mỗi kỳ, sau khi mọi dòng khác đã tính:

1. **Thặng dư** = (tổng nợ không kể liên NH + liên NH đầu kỳ + VCSH + LICĐKKS) − tổng TS không kể liên NH.
2. TG & cho vay liên NH (TS, BSB98) nhận thặng dư, nhưng không thấp hơn **sàn** = `min_interbank_assets_ratio` × TG KH.
3. Thặng dư không đủ đạt sàn thì phần thiếu là **vay liên NH tự động** (nợ, BSB112), trong trần
   `max_interbank_liab_to_assets` × tổng TS. Vượt trần thì phần còn thiếu là **funding gap**: kịch bản không khả
   thi, có cảnh báo, không bù.
4. Có thặng dư thì trả phần vay liên NH tự động đã rút trước, phần còn lại mới gửi liên NH.
5. Công bố từng kỳ: số dư tự động, sàn, trần, funding gap. Lãi trên phần tự động = lợi suất/chi phí vốn × số dư
   **đầu kỳ** (không lặp).

Bước 2 của phi tài chính (tự kéo giả định về biên lịch sử) **chưa áp** cho ngân hàng ở bản đầu. Đề xuất chỉ cảnh
báo (LDR, tăng vay liên NH tự động > ngưỡng), vì đòn bẩy của ngân hàng là LDR/huy động, cần owner chọn trước.

## 6. Kiểm tra (mã `BANK-*`)

| Mã | Mức | Điều kiện |
|---|---|---|
| `unbalanced` | lỗi | Tổng TS ≠ tổng nguồn vốn (dùng lại) |
| BANK-BS-01 | lỗi | Dòng TS âm (tiền mặt, TG NHNN, dư nợ, CK, liên NH TS) |
| BANK-FUND-01 | cảnh báo | Còn funding gap sau cân thanh khoản (không khả thi) |
| BANK-LIQ-01 | cảnh báo | LDR dự phóng vượt mức cao nhất lịch sử, hoặc vay liên NH tự động tăng > 15% so với kỳ trước |
| BANK-AQ-01 | cảnh báo | Xoá nợ ngụ ý âm (dự phòng cuối kỳ > đầu kỳ + trích lập) |
| BANK-CAP-01 | cảnh báo | VCSH/tổng TS thấp hơn mức thấp nhất lịch sử. **CAR không có trong dữ liệu** (NOB151 = 0), nên L1 không tính CAR; CAR theo card cần CAR gốc do analyst nhập (tùy chọn, mục 8) |
| REV-10, ONE-01/02 | như phi tài chính | YTD so với cả năm; khoản bất thường ở dòng ngoài lãi khác |

Trạng thái "có thể công bố" = không lỗi, BS cân, khả thi. Bỏ điều kiện khớp tiền LCTT vì không có CF.

## 7. Kiến trúc code: kế thừa gì, thêm gì

**Mới (nhóm ngân hàng):**
- `config/families/bank/{lines,bs_lines,items}.csv`;
- `quick_model/families/bank/`: `BankFamily(StatementFamily)`, `BankEngine(PeriodEngine)`, `BankMetrics`, `methods.py` (class theo kind), `balancing.py` (cân thanh khoản);
- `config/templates/bank/template.json` + `survey.json`;
- `companies.json`: TCB → template `bank` → `raw: tcb`, `demo: false`;
- rule `BANK-*`.

**Dùng lại nguyên (không sửa):**
- `periods`, `Scenario` / `AssumptionValue`;
- template kế thừa, đăng ký method theo kind;
- `Driver` / `ctx.trace`, explain (dòng tổng / lợi nhuận / biên do family khai báo);
- default hội tụ / EMA / trung vị / min / max / latest;
- rules engine, `scenario_store`, modeldoc;
- `TaxEquityMethod` (chỉ đổi mã dòng).

**Tổng quát hoá nhỏ (phi tài chính giữ nguyên hành vi, có test khóa):**
1. Nạp lịch sử theo danh sách báo cáo của family: bank = BS + IS (+ thuyết minh tùy chọn, chuẩn hoá cột CamelCase). Không đòi CF.
2. Default cố định + biến đổi nhận tên chỉ số "doanh thu" làm tham số (bank dùng TOI).
3. Dải kịch bản: dòng hiển thị do family khai báo (bank: TOI, LN trước dự phòng, LNST mẹ).
4. Dashboard: tab LCTT và kiểm tra khớp tiền chỉ hiện khi family có CF; thêm khối "Cân thanh khoản". Header, lưới, thẻ block, giải thích ô, dải, khoản bất thường, lưu kịch bản dùng chung.

**Tần suất:** đề xuất **chạy theo năm trước** (mặc định), làm theo quý sau khi duyệt kết quả năm. Engine viết qua
period helper, nên phần quý chủ yếu là kiểm thử thêm.

## 8. Điểm cần owner quyết

| # | Câu hỏi | Đề xuất |
|---|---|---|
| D1 | Huy động mặc định theo **LDR hội tụ** hay theo **tăng trưởng TG KH** (như card)? | LDR. Hai default độc lập (TCB: tín dụng EMA ~22%, TG KH ~17%) làm vay liên NH tự động phình ra. Có thể thêm `funding.L1.growth` làm method thay thế |
| D2 | Dòng cân = **liên NH TS/nợ tự động** (sàn/trần, funding gap) thay biến cân tiền mặt của card? | Đồng ý (CLAUDE.md cấm biến cân ẩn) |
| D3 | NII L1 = **lợi suất + chi phí vốn riêng** hay **một NIM**? | Tách hai phía: vẫn là L1, giải thích được NIM đổi do phía nào, khớp L3 của card khi gộp |
| D4 | Default lợi suất / chi phí vốn: **hội tụ về EMA** hay **thực tế kỳ liền trước** (như lãi suất vay phi tài chính)? | Hội tụ: NIM là biên của ngân hàng |
| D5 | Trần tăng trưởng tín dụng dài hạn | 12% (cần owner chốt con số) |
| D6 | Chi phí HĐ: **cố định + biến đổi theo TOI** (dùng lại CPBH/CPQL) hay **CIR hội tụ**? | Cố định + biến đổi, lỗi thì CIR trung vị |
| D7 | Dự phòng: **chi phí tín dụng** (P&L) + **tỷ lệ bao phủ** (BS), xoá nợ ngụ ý là phần dư được công bố | Đồng ý; nợ xấu theo nhóm để L3 (thuyết minh chỉ có từ 2023) |
| D8 | CAR | Không tính ở L1; tùy chọn analyst nhập CAR gốc và dùng công thức card (CAR × Δ(VCSH/TTS)) như một chỉ số tham chiếu |
| D9 | Theo năm trước, theo quý sau | Đồng ý |
| D10 | Tự kéo giả định khi vay liên NH tăng mạnh (bước 2) | Chưa làm ở bản đầu, chỉ cảnh báo |

---
Tài liệu nội bộ, bản thiết kế chờ duyệt; số liệu tham chiếu TCB tính từ BCTC công bố, cần kiểm chứng trước khi dùng ngoài bộ phận.
