/**
 * Print filtered DataTable rows in an invoice-style document.
 * Only rows matching the current Search box (and filters) are printed.
 *
 * Usage:
 *   PrintFiltered.print({
 *     table: '#sales-table',          // or HTMLElement / DataTable API
 *     title: 'Sales Report',
 *     subtitle: 'Period: Today',
 *     partyLabel: 'Customer',         // optional left box title
 *     partyName: '…',
 *     partyMeta: 'phone / address',
 *     skipSelectors: ['.col-actions'],
 *     sumColumns: [5, 6, 7],          // 0-based indexes among printed columns
 *     sumLabels: ['Total', 'Paid', 'Due'],
 *   });
 */
(function (global) {
  function escapeHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function business() {
    return global.BUSINESS_INFO || {
      company_name: 'Mian Brother Fertilizer',
      company_address: '',
      company_phone: '',
      company_email: '',
    };
  }

  function getDataTable(table) {
    if (!global.jQuery) return null;
    const $ = global.jQuery;
    const $table = $(table);
    if (!$table.length) return null;
    if ($.fn.dataTable.isDataTable($table)) {
      return $table.DataTable();
    }
    return null;
  }

  function cellText(cell) {
    if (!cell) return '';
    // Prefer visible text; strip nested action controls
    const clone = cell.cloneNode(true);
    clone.querySelectorAll('button, .table-actions, .btn, script').forEach((el) => el.remove());
    return (clone.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function columnIndexesToPrint(headerRow, skipSelectors) {
    const skips = skipSelectors || ['.col-actions'];
    const indexes = [];
    Array.from(headerRow.children).forEach((th, i) => {
      const skip = skips.some((sel) => th.matches(sel) || th.classList.contains('col-actions'));
      if (!skip) indexes.push(i);
    });
    return indexes;
  }

  function parseMoney(text) {
    const n = parseFloat(String(text).replace(/[^0-9.-]/g, ''));
    return Number.isFinite(n) ? n : 0;
  }

  function formatMoney(n) {
    return Number(n || 0).toFixed(2);
  }

  function print(opts) {
    const options = opts || {};
    const tableEl =
      typeof options.table === 'string'
        ? document.querySelector(options.table)
        : options.table;
    if (!tableEl) {
      alert('Nothing to print.');
      return;
    }

    const dt = getDataTable(tableEl);
    const headerRow = tableEl.tHead?.rows?.[0];
    if (!headerRow) {
      alert('Nothing to print.');
      return;
    }

    const colIdx = columnIndexesToPrint(headerRow, options.skipSelectors);
    const headers = colIdx.map((i) => cellText(headerRow.children[i]));

    let bodyRows = [];
    if (dt) {
      // All filtered rows across pages — not just the current page
      dt.rows({ search: 'applied' })
        .nodes()
        .each(function (tr) {
          bodyRows.push(tr);
        });
    } else {
      bodyRows = Array.from(tableEl.tBodies?.[0]?.rows || []);
    }

    if (!bodyRows.length) {
      alert('No rows to print. Adjust your search and try again.');
      return;
    }

    const rowsHtml = bodyRows
      .map((tr) => {
        const cells = colIdx
          .map((i) => {
            const td = tr.children[i];
            const align = td?.classList?.contains('text-end') ? ' class="num"' : '';
            return `<td${align}>${escapeHtml(cellText(td))}</td>`;
          })
          .join('');
        return `<tr>${cells}</tr>`;
      })
      .join('');

    // Optional totals: sumColumns = indexes among *printed* columns
    let totalsHtml = '';
    const sumColumns = options.sumColumns || [];
    if (sumColumns.length && colIdx.length) {
      const sumSet = new Set(sumColumns);
      const sums = {};
      sumColumns.forEach((i) => {
        sums[i] = 0;
      });
      bodyRows.forEach((tr) => {
        sumColumns.forEach((printedIdx) => {
          const srcIdx = colIdx[printedIdx];
          if (srcIdx == null) return;
          sums[printedIdx] += parseMoney(cellText(tr.children[srcIdx]));
        });
      });
      const cells = colIdx
        .map((_, printedIdx) => {
          if (printedIdx === 0 && !sumSet.has(0)) {
            return `<td class="label"><strong>Totals</strong></td>`;
          }
          if (sumSet.has(printedIdx)) {
            return `<td class="num"><strong>${formatMoney(sums[printedIdx])}</strong></td>`;
          }
          return `<td></td>`;
        })
        .join('');
      totalsHtml = `<tr class="totals">${cells}</tr>`;
    }

    const biz = business();
    const title = escapeHtml(options.title || 'Report');
    const subtitle = escapeHtml(options.subtitle || '');
    const searchNote = dt
      ? `Showing ${bodyRows.length} of ${dt.rows().count()} record(s)`
      : `${bodyRows.length} record(s)`;
    const filterText = dt?.search?.() ? `Search: “${escapeHtml(dt.search())}”` : 'All listed rows';
    const printedAt = new Date().toLocaleString();

    const partyBlock =
      options.partyName
        ? `<div class="party">
            <div class="party-label">${escapeHtml(options.partyLabel || 'Party')}</div>
            <div class="party-name">${escapeHtml(options.partyName)}</div>
            ${options.partyMeta ? `<div class="meta">${escapeHtml(options.partyMeta)}</div>` : ''}
          </div>`
        : '';

    const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>${title}</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: "Segoe UI", Arial, sans-serif; color: #111; font-size: 12px; margin: 0; padding: 16px; }
    .wrap { max-width: 960px; margin: 0 auto; }
    .header { display: flex; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
    .brand { font-size: 18px; font-weight: 700; letter-spacing: -0.02em; }
    .meta { color: #555; font-size: 11px; line-height: 1.45; }
    .doc-title { font-size: 16px; font-weight: 700; margin: 0 0 4px; }
    .party { background: #f7f7e8; border: 1px solid #e5e5c8; padding: 10px 12px; margin-bottom: 14px; max-width: 52%; }
    .party-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: #666; margin-bottom: 2px; }
    .party-name { font-weight: 700; font-size: 13px; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; }
    th, td { border-bottom: 1px solid #ddd; padding: 7px 6px; vertical-align: top; }
    th { text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: #444; border-bottom: 2px solid #222; }
    td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
    tfoot td { border-bottom: none; padding-top: 8px; }
    tr.totals td { font-weight: 700; border-top: 1px solid #222; }
    tr.totals td.label { text-align: right; }
    .footer { margin-top: 18px; color: #666; font-size: 10px; display: flex; justify-content: space-between; }
    .no-print { text-align: center; margin-bottom: 12px; }
    @media print {
      .no-print { display: none !important; }
      body { padding: 0; }
    }
  </style>
</head>
<body>
  <div class="no-print">
    <button onclick="window.print()">Print</button>
    <button onclick="window.close()">Close</button>
  </div>
  <div class="wrap">
    <div class="header">
      <div>
        <div class="brand">${escapeHtml(biz.company_name || '')}</div>
        <div class="meta">${escapeHtml(biz.company_address || '')}</div>
        <div class="meta">${escapeHtml([biz.company_phone, biz.company_email].filter(Boolean).join(' · '))}</div>
      </div>
      <div style="text-align:right">
        <div class="doc-title">${title}</div>
        ${subtitle ? `<div class="meta">${subtitle}</div>` : ''}
        <div class="meta">${escapeHtml(searchNote)}</div>
        <div class="meta">${filterText}</div>
        <div class="meta">Printed: ${escapeHtml(printedAt)}</div>
      </div>
    </div>
    ${partyBlock}
    <table>
      <thead>
        <tr>${headers.map((h, i) => {
          const th = headerRow.children[colIdx[i]];
          const num = th?.classList?.contains('text-end') ? ' class="num"' : '';
          return `<th${num}>${escapeHtml(h)}</th>`;
        }).join('')}</tr>
      </thead>
      <tbody>${rowsHtml}</tbody>
      ${totalsHtml ? `<tfoot>${totalsHtml}</tfoot>` : ''}
    </table>
    <div class="footer">
      <span>${escapeHtml(biz.company_name || '')}</span>
      <span>End of report</span>
    </div>
  </div>
  <script>
    window.addEventListener('load', function () {
      setTimeout(function () { window.print(); }, 200);
    });
  <\/script>
</body>
</html>`;

    const win = global.open('', '_blank', 'noopener,noreferrer,width=960,height=700');
    if (!win) {
      alert('Please allow pop-ups to print.');
      return;
    }
    win.document.open();
    win.document.write(html);
    win.document.close();
  }

  global.PrintFiltered = { print };
})(window);
