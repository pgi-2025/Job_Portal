# -*- coding: utf-8 -*-
import sys, re

with open("admin-dashboard.html", "r", encoding="utf-8") as f:
    content = f.read()

# 1. HTML REPLACEMENT FOR #p-dy
html_dy_target = """        <!-- Daily Updates Pane (Modern SaaS Redesign) -->
        <div class="pane" id="p-dy">"""

html_dy_end = """        <!-- Day Detail Drawer (Right slide-over) -->"""

idx_start = content.find(html_dy_target)
idx_end = content.find(html_dy_end)

if idx_start == -1 or idx_end == -1:
    print(f"Error finding p-dy HTML region! start={idx_start}, end={idx_end}")
    sys.exit(1)

new_p_dy_html = """        <!-- Daily Updates Pane (Executive SaaS Redesign) -->
        <div class="pane" id="p-dy">
          <!-- Page Header -->
          <div class="daily-page-header">
            <div class="daily-header-left">
              <h2>Daily Updates & Operational Velocity</h2>
              <div class="daily-header-sub">
                <span class="daily-pulse-dot"></span>
                <span id="dailySubtitleDate">Academic Year 2025–2026 Telemetry</span>
                <span style="color:#b2c4ba">•</span>
                <span style="font-size:12px;color:#7b8e84" id="dailyLiveClock">IST</span>
              </div>
            </div>
            <div class="daily-header-actions">
              <div class="daily-range-bar" id="dailyRangeBar">
                <button type="button" class="daily-range-btn" data-drange="today" onclick="setDailyRange('today')">Today</button>
                <button type="button" class="daily-range-btn" data-drange="7d" onclick="setDailyRange('7d')">7 Days</button>
                <button type="button" class="daily-range-btn active" data-drange="30d" onclick="setDailyRange('30d')">30 Days</button>
                <button type="button" class="daily-range-btn" data-drange="90d" onclick="setDailyRange('90d')">90 Days</button>
                <button type="button" class="daily-range-btn" data-drange="custom" onclick="setDailyRange('custom')">Custom</button>
                <div class="daily-custom-inputs" id="dailyCustomInputs">
                  <input type="date" id="dailyFromDate" onchange="onDailyCustomDateChange()">
                  <span style="font-size:12px;color:#7b8e84">to</span>
                  <input type="date" id="dailyToDate" onchange="onDailyCustomDateChange()">
                </div>
              </div>
              <button type="button" class="daily-btn-report" onclick="exportDailyAnalyticsPDF()">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>
                <span>Executive Analytics PDF</span>
              </button>
            </div>
          </div>

          <!-- Executive KPI Cards Grid (5 Cards) -->
          <div class="daily-kpi-grid" id="dailyKpiGrid">
            <!-- 1. Registered -->
            <div class="daily-kpi-card" onclick="filterTableByMetric('registered')" title="Inspect registration velocity">
              <div class="daily-kpi-card-head">
                <div class="daily-kpi-head-left">
                  <div class="daily-kpi-icon" style="background:#eef7f2;color:#093323;">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 10v6M2 10l10-5 10 5-10 5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/></svg>
                  </div>
                  <div>
                    <div class="daily-kpi-metric-title">STUDENT SIGNUPS</div>
                    <div class="daily-kpi-period-tag" id="periodTagReg">Last 30 Days</div>
                  </div>
                </div>
                <div class="daily-kpi-spark" id="sparkReg"></div>
              </div>
              <div class="daily-kpi-big-row">
                <div class="daily-kpi-val" id="kpiValReg">0</div>
                <div class="daily-kpi-val-sub" id="kpiValSubReg">total enrolled</div>
              </div>
              <div class="daily-kpi-today-bar">
                <div class="daily-kpi-today-label">
                  <span class="daily-pulse-dot"></span>
                  <span>Today: <strong id="kpiTodayCountReg">0</strong></span>
                </div>
                <span class="daily-delta-badge neu" id="deltaBadgeReg">—</span>
              </div>
            </div>

            <!-- 2. Attended Test -->
            <div class="daily-kpi-card" onclick="filterTableByMetric('attended')" title="Inspect assessment attempts">
              <div class="daily-kpi-card-head">
                <div class="daily-kpi-head-left">
                  <div class="daily-kpi-icon" style="background:#e0f2fe;color:#0284c7;">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>
                  </div>
                  <div>
                    <div class="daily-kpi-metric-title">TEST ATTENDEES</div>
                    <div class="daily-kpi-period-tag" id="periodTagAtt">Last 30 Days</div>
                  </div>
                </div>
                <div class="daily-kpi-spark" id="sparkAtt"></div>
              </div>
              <div class="daily-kpi-big-row">
                <div class="daily-kpi-val" id="kpiValAtt">0</div>
                <div class="daily-kpi-val-sub" id="kpiValSubAtt">domain takers</div>
              </div>
              <div class="daily-kpi-today-bar">
                <div class="daily-kpi-today-label">
                  <span class="daily-pulse-dot" style="background:#0284c7;box-shadow:0 0 0 2px rgba(2,132,199,0.2)"></span>
                  <span>Today: <strong id="kpiTodayCountAtt">0</strong></span>
                </div>
                <span class="daily-delta-badge neu" id="deltaBadgeAtt">—</span>
              </div>
            </div>

            <!-- 3. Passed -->
            <div class="daily-kpi-card" onclick="filterTableByMetric('passed')" title="Inspect qualified domain assessments">
              <div class="daily-kpi-card-head">
                <div class="daily-kpi-head-left">
                  <div class="daily-kpi-icon" style="background:#d1fae5;color:#059669;">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
                  </div>
                  <div>
                    <div class="daily-kpi-metric-title">TESTS PASSED</div>
                    <div class="daily-kpi-period-tag" id="periodTagPass">Last 30 Days</div>
                  </div>
                </div>
                <div class="daily-kpi-spark" id="sparkPass"></div>
              </div>
              <div class="daily-kpi-big-row">
                <div class="daily-kpi-val" id="kpiValPass">0</div>
                <div class="daily-kpi-val-sub" id="kpiValSubPass">qualified eligible</div>
              </div>
              <div class="daily-kpi-today-bar">
                <div class="daily-kpi-today-label">
                  <span class="daily-pulse-dot" style="background:#059669;box-shadow:0 0 0 2px rgba(5,150,105,0.2)"></span>
                  <span>Today: <strong id="kpiTodayCountPass">0</strong></span>
                </div>
                <span class="daily-delta-badge neu" id="deltaBadgePass">—</span>
              </div>
            </div>

            <!-- 4. Failed -->
            <div class="daily-kpi-card" onclick="filterTableByMetric('failed')" title="Inspect attempts below cutoff">
              <div class="daily-kpi-card-head">
                <div class="daily-kpi-head-left">
                  <div class="daily-kpi-icon" style="background:#fee2e2;color:#dc2626;">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
                  </div>
                  <div>
                    <div class="daily-kpi-metric-title">BELOW CUTOFF</div>
                    <div class="daily-kpi-period-tag" id="periodTagFail">Last 30 Days</div>
                  </div>
                </div>
                <div class="daily-kpi-spark" id="sparkFail"></div>
              </div>
              <div class="daily-kpi-big-row">
                <div class="daily-kpi-val" id="kpiValFail">0</div>
                <div class="daily-kpi-val-sub" id="kpiValSubFail">retest recommended</div>
              </div>
              <div class="daily-kpi-today-bar">
                <div class="daily-kpi-today-label">
                  <span class="daily-pulse-dot" style="background:#dc2626;box-shadow:0 0 0 2px rgba(220,38,38,0.2)"></span>
                  <span>Today: <strong id="kpiTodayCountFail">0</strong></span>
                </div>
                <span class="daily-delta-badge neu" id="deltaBadgeFail">—</span>
              </div>
            </div>

            <!-- 5. Retest Revenue -->
            <div class="daily-kpi-card" onclick="filterTableByMetric('payments')" title="Inspect retest collections">
              <div class="daily-kpi-card-head">
                <div class="daily-kpi-head-left">
                  <div class="daily-kpi-icon" style="background:#fef3c7;color:#d97706;">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="5" width="20" height="14" rx="2"></rect><line x1="2" y1="10" x2="22" y2="10"></line></svg>
                  </div>
                  <div>
                    <div class="daily-kpi-metric-title">RETEST REVENUE</div>
                    <div class="daily-kpi-period-tag" id="periodTagPay">Last 30 Days</div>
                  </div>
                </div>
                <div class="daily-kpi-spark" id="sparkPay"></div>
              </div>
              <div class="daily-kpi-big-row">
                <div class="daily-kpi-val" id="kpiValPay">₹0</div>
                <div class="daily-kpi-val-sub" id="kpiValSubPay">collections</div>
              </div>
              <div class="daily-kpi-today-bar">
                <div class="daily-kpi-today-label">
                  <span class="daily-pulse-dot" style="background:#d97706;box-shadow:0 0 0 2px rgba(217,119,6,0.2)"></span>
                  <span>Today: <strong id="kpiTodayCountPay">0</strong></span>
                </div>
                <span class="daily-delta-badge neu" id="deltaBadgePay">—</span>
              </div>
            </div>
          </div>

          <!-- Executive Analytics Ribbon (4 Tiles) -->
          <div class="daily-exec-ribbon">
            <div class="daily-exec-tile">
              <div class="daily-exec-tile-icon" style="background:#eef7f2;color:#093323;">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"></polyline><polyline points="17 6 23 6 23 12"></polyline></svg>
              </div>
              <div class="daily-exec-tile-content">
                <div class="daily-exec-tile-label">AVERAGE INFLUX PACE</div>
                <div class="daily-exec-tile-val"><span id="insAvgReg">0</span> <small>/ day</small></div>
                <div class="daily-exec-tile-sub">Signups averaged across current window</div>
              </div>
            </div>
            <div class="daily-exec-tile">
              <div class="daily-exec-tile-icon" style="background:#fef3c7;color:#b45309;">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"></path><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"></path><path d="M4 22h16"></path><path d="M10 14.66V17c0 .55-.45 1-1 1H8c-.55 0-1 .45-1 1s.45 1 1 1h8c.55 0 1-.45 1-1s-.45-1-1-1h-1c-.55 0-1-.45-1-1v-2.34"></path><path d="M18 4H6v7a6 6 0 0 0 12 0V4z"></path></svg>
              </div>
              <div class="daily-exec-tile-content">
                <div class="daily-exec-tile-label">PEAK REGISTRATION DAY</div>
                <div class="daily-exec-tile-val"><span id="insBestDay">–</span></div>
                <div class="daily-exec-tile-sub" id="insBestSub">Highest single-day enrollment volume</div>
              </div>
            </div>
            <div class="daily-exec-tile">
              <div class="daily-exec-tile-icon" style="background:#e0f2fe;color:#0284c7;">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="6"></circle><circle cx="12" cy="12" r="2"></circle></svg>
              </div>
              <div class="daily-exec-tile-content">
                <div class="daily-exec-tile-label">FUNNEL CONVERSION</div>
                <div class="daily-exec-tile-val"><span id="insConv">0%</span> <small>activation</small></div>
                <div class="daily-exec-tile-sub" id="insConvSub">Enrolled students who took tests</div>
              </div>
            </div>
            <div class="daily-exec-tile">
              <div class="daily-exec-tile-icon" style="background:#d1fae5;color:#059669;">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>
              </div>
              <div class="daily-exec-tile-content">
                <div class="daily-exec-tile-label">DOMAIN PASS RATE</div>
                <div class="daily-exec-tile-val"><span id="insPassRate">0%</span> <small id="insPassRatio"></small></div>
                <div class="daily-exec-tile-sub">Assessments scoring above domain cutoff</div>
              </div>
            </div>
          </div>

          <!-- Trend Chart Card (Chart.js Modern SaaS Overhaul) -->
          <div class="daily-chart-card">
            <div class="daily-chart-head">
              <div class="daily-chart-title-box">
                <h3>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"></polyline><polyline points="17 6 23 6 23 12"></polyline></svg>
                  <span>Placement Velocity & Assessment Telemetry</span>
                </h3>
                <p id="dailyChartSubtitle">Daily student registrations vs test attempts across the selected timeframe</p>
              </div>
              <div class="daily-chart-controls">
                <div class="daily-chart-toggle-group">
                  <button type="button" class="daily-chart-toggle-btn active" id="btnModeStandard" onclick="setDailyChartMode(false)">Activity & Attendance</button>
                  <button type="button" class="daily-chart-toggle-btn" id="btnModeStacked" onclick="setDailyChartMode(true)">Passed vs Failed</button>
                </div>
              </div>
            </div>
            <div style="position:relative;height:280px;width:100%">
              <canvas id="dailyChartCanvas"></canvas>
            </div>
          </div>

          <!-- Day-Wise Historical Table Card -->
          <div class="daily-table-card">
            <div class="daily-table-toolbar">
              <div class="daily-table-title">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>
                <span>Day-by-Day Historical Log</span>
                <span class="op-tag" id="dailyTableCount">30 days</span>
              </div>
              <div class="daily-table-actions">
                <select class="db-select" id="dailyPageSize" onchange="setDailyPageSize(this.value)">
                  <option value="15">15 rows</option>
                  <option value="30" selected>30 rows</option>
                  <option value="60">60 rows</option>
                  <option value="999">All rows</option>
                </select>
                <button type="button" class="db-btn" onclick="exportDaily('csv')" title="Export table to CSV">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                  <span>CSV</span>
                </button>
                <button type="button" class="db-btn" onclick="exportDaily('xlsx')" title="Export table to Excel">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                  <span>Excel</span>
                </button>
                <button type="button" class="db-btn db-btn-primary" onclick="exportDailyAnalyticsPDF()" title="Export executive report to PDF">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>
                  <span>Report PDF</span>
                </button>
              </div>
            </div>

            <!-- Table Scroll Wrapper -->
            <div class="daily-table-scroll">
              <table class="daily-tbl" id="dailyMainTable">
                <thead>
                  <tr>
                    <th onclick="sortDailyTable('date')">Date <span id="sort-d-date">↓</span></th>
                    <th onclick="sortDailyTable('registered')">Registered <span id="sort-d-registered">↕</span></th>
                    <th onclick="sortDailyTable('attended')">Attended Test <span id="sort-d-attended">↕</span></th>
                    <th onclick="sortDailyTable('passed')">Passed <span id="sort-d-passed">↕</span></th>
                    <th onclick="sortDailyTable('failed')">Failed <span id="sort-d-failed">↕</span></th>
                    <th onclick="sortDailyTable('rate')">Pass Rate <span id="sort-d-rate">↕</span></th>
                    <th onclick="sortDailyTable('payments')">Payments <span id="sort-d-payments">↕</span></th>
                    <th onclick="sortDailyTable('revenue_inr')">Revenue <span id="sort-d-revenue_inr">↕</span></th>
                  </tr>
                </thead>
                <tbody id="dailyTableBody">
                  <!-- Populated dynamically -->
                </tbody>
                <tfoot id="dailyTableFoot">
                  <!-- Sticky TOTAL Row -->
                </tfoot>
              </table>
            </div>

            <!-- Pagination Controls -->
            <div class="db-pagination" style="margin-top:14px">
              <div class="db-pagination-info" id="dailyPaginationInfo">Showing 1–30 of 30 days</div>
              <div class="db-page-controls" id="dailyPageControls"></div>
            </div>
          </div>
        </div>
\n        """

content = content[:idx_start] + new_p_dy_html + content[idx_end:]
print("Updated HTML markup for #p-dy.")

with open("admin-dashboard.html", "w", encoding="utf-8") as f:
    f.write(content)

