(function () {
  const d = window.dashboardCharts || {};
  const el = document.getElementById('profitChart');
  if (!el) return;

  const labels = d.labels || [];
  const money = (v) => Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  new Chart(el, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'Income', data: d.income || [], borderColor: '#1f7a4d', backgroundColor: '#1f7a4d33', tension: 0.3, fill: false },
        { label: 'Cost', data: d.cost || [], borderColor: '#6b7280', backgroundColor: '#6b728033', tension: 0.3, fill: false },
        { label: 'Expense', data: d.expense || [], borderColor: '#dc3545', backgroundColor: '#dc354533', tension: 0.3, fill: false },
        { label: 'Gross Profit', data: d.gross || [], borderColor: '#0d6efd', backgroundColor: '#0d6efd33', tension: 0.3, fill: false },
        { label: 'Net Profit', data: d.net || [], borderColor: '#f59e0b', backgroundColor: '#f59e0b33', tension: 0.3, fill: false },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'bottom' },
        tooltip: {
          callbacks: {
            label(ctx) {
              return `${ctx.dataset.label}: ${money(ctx.parsed.y)}`;
            },
          },
        },
      },
      scales: {
        y: {
          ticks: {
            callback(value) {
              return money(value);
            },
          },
        },
      },
    },
  });
})();
