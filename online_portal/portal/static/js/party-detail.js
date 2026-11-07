(function () {
  const PIE_COLORS = ['#1a73e8', '#34a853', '#f9ab00', '#ea4335', '#9334e6'];
  const fontFamily = "'Segoe UI', system-ui, -apple-system, sans-serif";

  const money = (v) =>
    Number(v || 0).toLocaleString(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    });

  const formatPieValue = (value, row) => {
    if (row && row.is_count) return String(Math.round(Number(value) || 0));
    return money(value);
  };

  function pieDisplayValues(rawValues, minShare = 0.1) {
    const raw = rawValues.map((v) => Math.max(Number(v) || 0, 0));
    const n = raw.length;
    if (!n) return [];
    const total = raw.reduce((a, b) => a + b, 0);
    if (total <= 0) return raw.map(() => 1);
    const floor = total * minShare;
    return raw.map((v) => Math.max(v, floor));
  }

  function applyPieSwatches(root) {
    (root || document).querySelectorAll('.dash-pie-swatch').forEach((el) => {
      const idx = Number(el.dataset.colorIdx || 0);
      el.style.backgroundColor = PIE_COLORS[idx % PIE_COLORS.length];
    });
  }

  function initPartyDetailPie() {
    const data = window.partyAccountPie || {};
    const canvas = document.getElementById('partyAccountPie');
    if (!canvas || !data.values || !data.values.length) return;

    const rawValues = data.values.map((v) => Number(v) || 0);
    const displayValues = pieDisplayValues(rawValues);
    const colors = data.labels.map((_, i) => PIE_COLORS[i % PIE_COLORS.length]);
    const pieRows = data.rows || [];

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
                const row = pieRows[ctx.dataIndex];
                const realTotal = rawValues.reduce((a, b) => a + b, 0);
                const pct = realTotal ? ((real / realTotal) * 100).toFixed(1) : '0';
                return ` ${ctx.label}: ${formatPieValue(real, row)} (${pct}%)`;
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
    initPartyDetailPie();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
