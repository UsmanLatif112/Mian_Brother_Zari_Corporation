(function () {
  const PIE_COLORS = ['#1a73e8', '#34a853', '#f9ab00', '#ea4335', '#9334e6', '#00acc1', '#ef6c00', '#5e35b1'];
  const tickColor = '#64748b';
  const fontFamily = "'Segoe UI', system-ui, -apple-system, sans-serif";
  const gridColor = 'rgba(148, 163, 184, 0.18)';

  const money = (v) =>
    Number(v || 0).toLocaleString(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    });

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

  function pieDisplayValues(rawValues, minShare = 0.08) {
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

  function initTrendChart() {
    const d = (window.inventoryProfitReport || {}).trend || {};
    const el = document.getElementById('profitTrendChart');
    if (!el) return;
    const labels = d.labels || [];

    new Chart(el, {
      type: 'line',
      data: {
        labels,
        datasets: [
          lineDataset('Sale', d.sale, '#1a73e8', { fill: true, bold: true }),
          lineDataset('COGS', d.cost, '#9aa0a6'),
          lineDataset('Inventory Loss', d.loss, '#ea4335'),
          lineDataset('Gross Profit', d.gross, '#34a853', { fill: true, bold: true }),
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
          },
          y: {
            beginAtZero: true,
            grid: { color: gridColor, drawBorder: false },
            border: { display: false },
            ticks: {
              color: tickColor,
              font: { family: fontFamily, size: 11 },
              callback: (v) => money(v),
            },
          },
        },
      },
    });
  }

  function initBarChart() {
    const d = (window.inventoryProfitReport || {}).categoryChart || {};
    const el = document.getElementById('profitBarChart');
    if (!el || !(d.labels || []).length) return;

    const profits = (d.profit || []).map((v) => Number(v) || 0);
    const colors = profits.map((v) => (v < 0 ? '#ea4335' : '#34a853'));

    new Chart(el, {
      type: 'bar',
      data: {
        labels: d.labels,
        datasets: [
          {
            label: 'Gross Profit',
            data: profits,
            backgroundColor: colors.map((c) => `${c}cc`),
            borderColor: colors,
            borderWidth: 1,
            borderRadius: 6,
            maxBarThickness: 42,
          },
        ],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: 'rgba(32, 33, 36, 0.96)',
            padding: 12,
            cornerRadius: 8,
            callbacks: {
              label(ctx) {
                const i = ctx.dataIndex;
                const sale = (d.sale || [])[i];
                const cost = (d.cost || [])[i];
                const margin = (d.margin || [])[i];
                return [
                  ` Profit: ${money(ctx.parsed.x)}`,
                  ` Sale: ${money(sale)}`,
                  ` COGS: ${money(cost)}`,
                  ` Margin: ${Number(margin || 0).toFixed(1)}%`,
                ];
              },
            },
          },
        },
        scales: {
          x: {
            grid: { color: gridColor, drawBorder: false },
            border: { display: false },
            ticks: {
              color: tickColor,
              font: { family: fontFamily, size: 11 },
              callback: (v) => money(v),
            },
          },
          y: {
            grid: { display: false },
            border: { display: false },
            ticks: {
              color: tickColor,
              font: { family: fontFamily, size: 11 },
              autoSkip: false,
            },
          },
        },
      },
    });
  }

  function initPieChart() {
    const data = (window.inventoryProfitReport || {}).pie || {};
    const el = document.getElementById('profitPieChart');
    if (!el || !(data.values || []).length) return;

    const colors = data.labels.map((_, i) => PIE_COLORS[i % PIE_COLORS.length]);
    const rawValues = data.values.map((v) => Number(v) || 0);
    const displayValues = pieDisplayValues(rawValues);

    new Chart(el, {
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
            padding: 12,
            cornerRadius: 8,
            callbacks: {
              label(ctx) {
                const raw = rawValues[ctx.dataIndex] || 0;
                const total = rawValues.reduce((a, b) => a + b, 0) || 1;
                const pct = ((raw / total) * 100).toFixed(1);
                return ` ${ctx.label}: ${money(raw)} (${pct}%)`;
              },
            },
          },
        },
      },
    });
  }

  applyPieSwatches();
  initTrendChart();
  initBarChart();
  initPieChart();
})();
