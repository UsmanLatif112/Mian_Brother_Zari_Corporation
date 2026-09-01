(function () {
  const tickColor = '#64748b';
  const fontFamily = "'Segoe UI', system-ui, -apple-system, sans-serif";

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
      borderDash: opts.dashed ? [8, 4] : undefined,
      pointRadius: showPoints ? 4 : 0,
      pointHoverRadius: 6,
      pointHitRadius: 12,
      pointBackgroundColor: color,
      pointBorderColor: '#fff',
      pointBorderWidth: 2,
      tension: 0.35,
      fill: opts.fill || false,
      order: opts.order,
    };
  }

  function buildLineChartOptions(d, labels, countLabel) {
    const gridColor = 'rgba(148, 163, 184, 0.18)';
    return {
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
            afterBody(items) {
              if (!items.length || !d.count || !countLabel) return [];
              const idx = items[0].dataIndex;
              const n = Number(d.count[idx] || 0);
              if (!n) return [];
              return [` ${countLabel}: ${n}`];
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
    };
  }

  function initVendorListChart() {
    const d = window.vendorListChart || {};
    const el = document.getElementById('vendorListChart');
    if (!el) return;
    const labels = d.labels || [];
    new Chart(el, {
      type: 'line',
      data: {
        labels,
        datasets: [
          lineDataset('Total Purchase', d.total, '#1a73e8', { fill: true, bold: true, order: 1 }),
          lineDataset('Paid', d.paid, '#34a853', { order: 2 }),
          lineDataset('Payable', d.payable, '#f9ab00', { dashed: true, bold: true, order: 3 }),
        ],
      },
      options: buildLineChartOptions(d, labels, 'Purchases'),
    });
  }

  function initCustomerListChart() {
    const d = window.customerListChart || {};
    const el = document.getElementById('customerListChart');
    if (!el) return;
    const labels = d.labels || [];
    new Chart(el, {
      type: 'line',
      data: {
        labels,
        datasets: [
          lineDataset('Total Sale', d.sale, '#1a73e8', { fill: true, bold: true, order: 1 }),
          lineDataset('Total Paid', d.paid, '#34a853', { order: 2 }),
          lineDataset('Total Credit', d.credit, '#f9ab00', { dashed: true, bold: true, order: 3 }),
        ],
      },
      options: buildLineChartOptions(d, labels, 'Sales'),
    });
  }

  function initInventoryListChart() {
    const d = window.inventoryListChart || {};
    const el = document.getElementById('inventoryListChart');
    if (!el) return;
    const labels = d.labels || [];
    new Chart(el, {
      type: 'line',
      data: {
        labels,
        datasets: [
          lineDataset('Stock Value', d.stock, '#1a73e8', { fill: true, bold: true, order: 1 }),
          lineDataset('Purchases', d.purchase, '#34a853', { order: 2 }),
          lineDataset('Sales', d.sale, '#ea4335', { order: 3 }),
        ],
      },
      options: buildLineChartOptions(d, labels, 'Purchases'),
    });
  }

  function initSalesmanListChart() {
    const d = window.salesmanListChart || {};
    const el = document.getElementById('salesmanListChart');
    if (!el) return;
    const labels = d.labels || [];
    new Chart(el, {
      type: 'line',
      data: {
        labels,
        datasets: [
          lineDataset('Total Sales', d.sales, '#1a73e8', { fill: true, bold: true, order: 1 }),
          lineDataset('Paid', d.paid, '#34a853', { order: 2 }),
          lineDataset('Credit Outstanding', d.credit, '#f9ab00', { dashed: true, bold: true, order: 3 }),
        ],
      },
      options: buildLineChartOptions(d, labels, 'Sales'),
    });
  }

  function boot() {
    if (typeof Chart === 'undefined') return;
    initVendorListChart();
    initCustomerListChart();
    initInventoryListChart();
    initSalesmanListChart();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
