"""Cấu hình của hai công cụ (một chỗ). Số của mô hình nằm trong quick_model/config, không lặp lại ở đây."""

GATE = {'texts': {'NO_RESULT': 'mô hình có lỗi chặn, không có số',
           'FUNDING_GAP': 'thiếu vốn {gap} tỷ đồng từ {period}',
           'UPSTREAM_NOT_PUBLISHABLE': 'mô hình đánh dấu chưa được công bố',
           'LATEST_QUARTER_NOT_USED': 'quý {raw} có trong dữ liệu nhưng bị bỏ, mô hình chỉ dùng đến {used}',
           'SCOPE_SUSPECT': 'nghi đổi phạm vi hợp nhất từ {q} ({what}): chỉ dùng năm {y}, các năm sau cần đặt lại giả định',
           'REBASED': 'đã đặt lại giả định theo các quý từ {q} vì đổi phạm vi hợp nhất ({what}); tăng trưởng doanh thu vẫn là của mô hình; cần '
                      'chuyên viên xem',
           'LOSS_IN_PATH': 'LNST mẹ không dương ở {what}: không nhân bội số được',
           'OVERRIDE_OUTSIDE_HISTORY': 'giả định đặt tay nằm ngoài khoảng lịch sử: {ids}'},
 'warn_codes_block': ('outside_history', 'high_growth'),
 'scope': {'min_before': 4, 'max_after': 4, 'minority_min_share': 0.05, 'minority_ratio': 0.25, 'jv_min_share': 0.1, 'jv_ratio': 3.0}}

BRIEF_YEARS = 2
HEADLINE = "npat_parent"
KEY_LINES = {'nonfinancial': ('net_revenue', 'gross_profit', 'pbt', 'npat_parent'), 'bank': ('nii', 'toi', 'provision_expense', 'npat_parent')}

VALUATION = {'reasons_allowed': ('REBASED',),
 'window_years': 5,
 'min_days': 250,
 'stale_days': 10,
 'stats': (('p10', '0.10'), ('p25', '0.25'), ('p50', '0.50'), ('p75', '0.75'), ('p90', '0.90')),
 'pb_roe_years': 3,
 'eps_growth_min': '0.20',
 'beta_trial': ('0.8', '1.0', '1.2'),
 'g_trial': ('0.02', '0.04'),
 'beta_range': ('0.3', '2.5'),
 'g_range': ('-0.02', '0.05'),
 'rerating_flag': '0.1',
 'associates_flag': 0.15,
 'typical_npat_error': {'nonfinancial': '0.31',
                        'bank': '0.16',
                        'basis': 'sai số trung vị LNST cả năm, backtest v2.6.3 (docs/phuong_phap/quy_trinh_L1.md)'},
 'unavailable': {'bank': {'ev_ebitda': 'không áp dụng cho ngân hàng',
                          'dcf_fcff': 'không áp dụng cho ngân hàng',
                          'dcf_fcfe': 'không áp dụng cho ngân hàng',
                          'ddm': 'mô hình không dự phóng cổ tức'},
                 'nonfinancial': {'ev_ebitda': 'chưa có lịch sử EV/EBITDA đo được',
                                  'dcf_fcff': 'dòng tiền của mô hình chưa khớp định nghĩa định giá (capex, vốn lưu động)',
                                  'dcf_fcfe': 'dòng tiền của mô hình chưa khớp định nghĩa định giá (capex, vốn lưu động)',
                                  'ddm': 'mô hình không dự phóng cổ tức',
                                  'sotp': 'chưa có bội số đo được cho phần công ty liên kết: cần chuyên viên'}}}
