(function () {

  const PIE_COLORS = ['#1a73e8', '#34a853', '#f9ab00', '#ea4335', '#9334e6'];

  const tickColor = '#64748b';

  const fontFamily = "'Segoe UI', system-ui, -apple-system, sans-serif";



  const money = (v) =>

    Number(v || 0).toLocaleString(undefined, {

      minimumFractionDigits: 0,

      maximumFractionDigits: 2,

    });



  /** Boost tiny slices so every segment stays visible in the doughnut. */
  function pieDisplayValues(rawValues, minShare = 0.1) {
    const raw = rawValues.map((v) => Math.max(Number(v) || 0, 0));
    const n = raw.length;
    if (!n) return [];
    const total = raw.reduce((a, b) => a + b, 0);
    if (total <= 0) return raw.map(() => 1);
    const floor = total * minShare;
    return raw.map((v) => Math.max(v, floor));
  }



  function applyPieSwatches() {

    document.querySelectorAll('.dash-pie-swatch').forEach((el) => {

      const idx = Number(el.dataset.colorIdx || 0);

      el.style.backgroundColor = PIE_COLORS[idx % PIE_COLORS.length];

    });

  }



  function initPieCharts() {

    const pies = window.dashboardPies || {};

    document.querySelectorAll('.dash-pie-canvas').forEach((canvas) => {

      const key = canvas.dataset.pieKey;

      const data = pies[key];

      if (!data || !data.values || !data.values.length) return;



      const colors = data.labels.map((_, i) => PIE_COLORS[i % PIE_COLORS.length]);

      const rawValues = data.values.map((v) => Number(v) || 0);

      const displayValues = pieDisplayValues(rawValues);



      new Chart(canvas, {

        type: 'doughnut',

        data: {

          labels: data.labels,

          datasets: [

            {

              data: displayValues,

              backgroundColor: colors,

              borderColor: '#fff',

              borderWidth: 2,

              hoverOffset: 6,

              spacing: 2,

            },

          ],

        },

        options: {

          responsive: true,

          maintainAspectRatio: false,

          cutout: '58%',

          plugins: {

            legend: { display: false },

            tooltip: {

              backgroundColor: 'rgba(32, 33, 36, 0.96)',

              titleColor: '#fff',

              bodyColor: '#e8eaed',

              borderColor: 'rgba(255,255,255,0.08)',

              borderWidth: 1,

              padding: 10,

              cornerRadius: 8,

              titleFont: { family: fontFamily, size: 12, weight: '600' },

              bodyFont: { family: fontFamily, size: 12 },

              callbacks: {

                label(ctx) {

                  const real = rawValues[ctx.dataIndex];

                  const realTotal = rawValues.reduce((a, b) => a + b, 0);

                  const pct = realTotal ? ((real / realTotal) * 100).toFixed(1) : '0';

                  return ` ${ctx.label}: ${money(real)} (${pct}%)`;

                },

              },

            },

          },

        },

      });

    });

  }



  function initLineChart() {

    const d = window.dashboardCharts || {};

    const el = document.getElementById('profitChart');

    if (!el) return;



    const labels = d.labels || [];

    const gridColor = 'rgba(148, 163, 184, 0.18)';



    function lineDataset(label, data, color, opts = {}) {

      const showPoints = (data || []).length <= 3;

      return {

        label,

        data: data || [],

        borderColor: color,

        backgroundColor: opts.fill ? `${color}22` : 'transparent',

        borderWidth: opts.bold ? 2.5 : 2,

        pointRadius: showPoints ? 4 : 0,

        pointHoverRadius: 6,

        pointHitRadius: 12,

        pointBackgroundColor: color,

        pointBorderColor: '#fff',

        pointBorderWidth: 2,

        tension: 0.35,

        fill: opts.fill || false,

        hidden: !!opts.hidden,

      };

    }



    new Chart(el, {

      type: 'line',

      data: {

        labels,

        datasets: [

          lineDataset('Income', d.income, '#1a73e8', { fill: true, bold: true }),

          lineDataset('Cost', d.cost, '#9aa0a6', { hidden: false }),

          lineDataset('Expense', d.expense, '#ea4335'),

          lineDataset('Gross Profit', d.gross, '#34a853'),

          lineDataset('Net Profit', d.net, '#f9ab00', { fill: true, bold: true }),

        ],

      },

      options: {

        responsive: true,

        maintainAspectRatio: false,

        interaction: { mode: 'index', intersect: false },

        plugins: {

          legend: {

            position: 'bottom',

            align: 'start',

            labels: {

              usePointStyle: true,

              pointStyle: 'circle',

              boxWidth: 8,

              boxHeight: 8,

              padding: 18,

              color: tickColor,

              font: { family: fontFamily, size: 12 },

            },

          },

          tooltip: {

            backgroundColor: 'rgba(32, 33, 36, 0.96)',

            titleColor: '#fff',

            bodyColor: '#e8eaed',

            borderColor: 'rgba(255,255,255,0.08)',

            borderWidth: 1,

            padding: 12,

            cornerRadius: 8,

            titleFont: { family: fontFamily, size: 13, weight: '600' },

            bodyFont: { family: fontFamily, size: 12 },

            displayColors: true,

            boxPadding: 4,

            callbacks: {

              title(items) {

                if (!items.length) return '';

                const idx = items[0].dataIndex;

                const label = labels[idx] || items[0].label || '';

                const gran = d.granularity === 'day' ? 'Daily' : 'Monthly';

                return `${label} · ${gran}`;

              },

              label(ctx) {

                return ` ${ctx.dataset.label}: ${money(ctx.parsed.y)}`;

              },

            },

          },

        },

        scales: {

          x: {

            type: 'category',

            offset: true,

            grid: { display: false, drawBorder: false },

            border: { display: false },

            ticks: {

              color: tickColor,

              font: { family: fontFamily, size: 11 },

              maxRotation: 0,

              autoSkip: true,

              maxTicksLimit: d.granularity === 'day' ? 10 : 12,

              padding: 8,

            },

            title: {

              display: labels.length > 0,

              text: d.granularity === 'day' ? 'Date' : 'Month',

              color: tickColor,

              font: { family: fontFamily, size: 11, weight: '500' },

              padding: { top: 4 },

            },

          },

          y: {

            beginAtZero: true,

            grid: { color: gridColor, drawBorder: false },

            border: { display: false },

            ticks: {

              color: tickColor,

              font: { family: fontFamily, size: 11 },

              padding: 8,

              callback(value) {

                return money(value);

              },

            },

          },

        },

      },

    });

  }



  function boot() {
    if (typeof Chart === 'undefined') return;
    applyPieSwatches();
    initLineChart();
    initPieCharts();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

})();

