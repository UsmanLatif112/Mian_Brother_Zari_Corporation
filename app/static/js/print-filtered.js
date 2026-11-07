/**
 * Print filtered DataTable rows in an invoice-style document.
 * Prints all data columns (skips Action only). Works with DataTables paging/search.
 *
 * Usage:
 *   PrintFiltered.print({
 *     table: '#sales-table',
 *     title: 'Sales Report',
 *     subtitle: 'Period: Today',
 *     partyLabel: 'Customer',
 *     partyName: '…',
 *     partyMeta: 'phone / address',
 *     partyFields: [{ label: 'Phone', value: '…' }, …],
 *     skipSelectors: ['.col-actions'],
 *     sumColumns: [5, 6, 7],
 *     extraTables: [{ title: 'Payments', table: '#payments-table' }],
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
      company_name: '',
      company_address: '',
      company_phone: '',
      company_email: '',
      logo_url: '',
      suite_name: 'Agri Books',
      developer_name: 'U. Technologies',
      developer_url: 'https://udottechnologies.com/',
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
    // Prefer full particulars (no "+N more") when provided for print/tooltip.
    const printText = cell.getAttribute?.('data-print-text');
    if (printText != null && String(printText).trim() !== '') {
      return String(printText).replace(/\s+/g, ' ').trim();
    }
    const clone = cell.cloneNode(true);
    clone.querySelectorAll('button, .table-actions, .btn, script, .dropdown').forEach((el) => el.remove());
    return (clone.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function columnIndexesToPrint(headerRow, skipSelectors) {
    const skips = skipSelectors || ['.col-actions', '.col-print-skip'];
    const indexes = [];
    Array.from(headerRow.children).forEach((th, i) => {
      const skip = skips.some(
        (sel) =>
          th.matches?.(sel) ||
          th.classList.contains('col-actions') ||
          th.classList.contains('col-print-skip')
      );
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

  function resolveTable(table) {
    if (!table) return null;
    if (typeof table === 'string') return document.querySelector(table);
    return table;
  }

  function extractTableHtml(tableEl, options) {
    const opts = options || {};
    const dt = getDataTable(tableEl);
    const headerRow = tableEl.tHead?.rows?.[0];
    if (!headerRow) return null;

    const colIdx = columnIndexesToPrint(headerRow, opts.skipSelectors);
    const headers = colIdx.map((i) => cellText(headerRow.children[i]));

    const bodyRows = [];
    if (dt) {
      // Prefer DataTables API so hidden/paged columns still print
      dt.rows({ search: 'applied' }).every(function () {
        const rowIdx = this.index();
        const cells = colIdx.map((col) => {
          const node = dt.cell(rowIdx, col).node();
          return cellText(node);
        });
        bodyRows.push(cells);
      });
    } else {
      Array.from(tableEl.tBodies?.[0]?.rows || []).forEach((tr) => {
        if (tr.querySelector('td[colspan]') && tr.cells.length === 1) return;
        bodyRows.push(colIdx.map((i) => cellText(tr.children[i])));
      });
    }

    if (!bodyRows.length) return { empty: true, headers, colIdx, headerRow, bodyRows, dt };

    const rowsHtml = bodyRows
      .map((cells) => {
        const tds = cells
          .map((text, printedIdx) => {
            const th = headerRow.children[colIdx[printedIdx]];
            const align = th?.classList?.contains('text-end') ? ' class="num"' : '';
            return `<td${align}>${escapeHtml(text)}</td>`;
          })
          .join('');
        return `<tr>${tds}</tr>`;
      })
      .join('');

    let totalsHtml = '';
    const sumColumns = opts.sumColumns || [];
    if (sumColumns.length && colIdx.length) {
      const sumSet = new Set(sumColumns);
      const sums = {};
      sumColumns.forEach((i) => {
        sums[i] = 0;
      });
      bodyRows.forEach((cells) => {
        sumColumns.forEach((printedIdx) => {
          if (cells[printedIdx] == null) return;
          sums[printedIdx] += parseMoney(cells[printedIdx]);
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

    const headHtml = headers
      .map((h, i) => {
        const th = headerRow.children[colIdx[i]];
        const num = th?.classList?.contains('text-end') ? ' class="num"' : '';
        return `<th${num}>${escapeHtml(h)}</th>`;
      })
      .join('');

    return {
      empty: false,
      dt,
      count: bodyRows.length,
      totalCount: dt ? dt.rows().count() : bodyRows.length,
      search: typeof dt?.search === 'function' ? dt.search() : '',
      tableHtml: `<table>
        <thead><tr>${headHtml}</tr></thead>
        <tbody>${rowsHtml}</tbody>
        ${totalsHtml ? `<tfoot>${totalsHtml}</tfoot>` : ''}
      </table>`,
    };
  }

  function print(opts) {
    const options = opts || {};
    const tableEl = resolveTable(options.table);
    if (!tableEl) {
      alert('Nothing to print.');
      return;
    }

    const main = extractTableHtml(tableEl, options);
    if (!main || main.empty) {
      alert('No rows to print. Adjust your search and try again.');
      return;
    }

    let extraHtml = '';
    (options.extraTables || []).forEach((extra) => {
      const el = resolveTable(extra.table);
      if (!el) return;
      const extracted = extractTableHtml(el, {
        skipSelectors: extra.skipSelectors || options.skipSelectors,
        sumColumns: extra.sumColumns || [],
      });
      if (!extracted || extracted.empty) return;
      extraHtml += `<div class="section-title">${escapeHtml(extra.title || 'Details')}</div>${extracted.tableHtml}`;
    });

    const biz = business();
    const title = escapeHtml(options.title || 'Report');
    const subtitle = escapeHtml(options.subtitle || '');
    let logoSrc = biz.logo_url || '';
    if (logoSrc && !/^https?:\/\//i.test(logoSrc)) {
      try {
        logoSrc = new URL(logoSrc, global.location.origin).href;
      } catch (_) {
        /* keep relative */
      }
    }

    const fields = (options.partyFields || []).filter((f) => f && (f.value || f.value === 0));
    const fieldsHtml = fields.length
      ? `<div class="fields">${fields
          .map(
            (f) => `<div class="field">
              <div class="field-label">${escapeHtml(f.label)}</div>
              <div class="field-value">${escapeHtml(f.value)}</div>
            </div>`
          )
          .join('')}</div>`
      : '';

    const partyBalance =
      options.partyBalance != null && options.partyBalance !== ''
        ? `<div class="party-balance">
            <div class="party-label">${escapeHtml(options.partyBalanceLabel || 'Current Balance')}</div>
            <div class="party-balance-value">${escapeHtml(options.partyBalance)}</div>
          </div>`
        : '';

    const partyMain =
      options.partyName
        ? `<div class="party-main">
            <div class="party-label">${escapeHtml(options.partyLabel || 'Party')}</div>
            <div class="party-name">${escapeHtml(options.partyName)}</div>
            ${options.partyMeta ? `<div class="meta">${escapeHtml(options.partyMeta)}</div>` : ''}
            ${fieldsHtml}
          </div>`
        : fieldsHtml
          ? `<div class="party-main">${fieldsHtml}</div>`
          : '';

    const partyBlock =
      partyMain || partyBalance
        ? `<div class="party">${partyMain}${partyBalance}</div>`
        : '';

    openPrintDocument({
      title,
      subtitle,
      logoSrc,
      biz,
      partyBlock,
      sectionTitle:
        options.sectionTitle !== false
          ? escapeHtml(options.sectionTitle || (options.partyName ? 'Ledger Entries' : 'Records'))
          : '',
      tableHtml: main.tableHtml,
      extraHtml,
      hideDocTitle: !!options.hideDocTitle,
    });
  }

  function buildTableHtmlFromRows(options) {
    const headers = options.headers || [];
    const rows = options.rows || [];
    const checkboxFirst = !!options.checkboxFirst;
    const numCols = options.numColumns || [];
    const numSet = new Set(numCols);
    const sumColumns = options.sumColumns || [];
    const sumSet = new Set(sumColumns);

    if (!headers.length || !rows.length) return null;

    const headHtml = headers
      .map((h, i) => {
        if (checkboxFirst && i === 0) return '<th class="check"></th>';
        const num = numSet.has(i) ? ' class="num"' : '';
        return `<th${num}>${escapeHtml(h)}</th>`;
      })
      .join('');

    const rowsHtml = rows
      .map((cells) => {
        const tds = cells
          .map((text, i) => {
            if (checkboxFirst && i === 0) {
              return '<td class="check"><span class="chk" aria-hidden="true"></span></td>';
            }
            const num = numSet.has(i) ? ' class="num"' : '';
            return `<td${num}>${escapeHtml(text)}</td>`;
          })
          .join('');
        return `<tr>${tds}</tr>`;
      })
      .join('');

    let totalsHtml = '';
    if (sumColumns.length) {
      const sums = {};
      sumColumns.forEach((i) => {
        sums[i] = 0;
      });
      rows.forEach((cells) => {
        sumColumns.forEach((i) => {
          sums[i] += parseMoney(cells[i]);
        });
      });
      const cells = headers
        .map((_, i) => {
          if (i === (checkboxFirst ? 1 : 0) && !sumSet.has(i)) {
            return `<td class="label"><strong>Totals</strong></td>`;
          }
          if (sumSet.has(i)) {
            return `<td class="num"><strong>${formatMoney(sums[i])}</strong></td>`;
          }
          return '<td></td>';
        })
        .join('');
      totalsHtml = `<tr class="totals">${cells}</tr>`;
    }

    return `<table>
      <thead><tr>${headHtml}</tr></thead>
      <tbody>${rowsHtml}</tbody>
      ${totalsHtml ? `<tfoot>${totalsHtml}</tfoot>` : ''}
    </table>`;
  }

  /**
   * Print a custom row list (not tied to a table's full columns).
   * Use checkboxFirst: true for an empty checkbox column at the start.
   */
  function printRows(opts) {
    const options = opts || {};
    const tableHtml = buildTableHtmlFromRows(options);
    if (!tableHtml) {
      alert('No rows to print. Adjust your search and try again.');
      return;
    }

    const biz = business();
    let logoSrc = biz.logo_url || '';
    if (logoSrc && !/^https?:\/\//i.test(logoSrc)) {
      try {
        logoSrc = new URL(logoSrc, global.location.origin).href;
      } catch (_) {
        /* keep relative */
      }
    }

    openPrintDocument({
      title: escapeHtml(options.title || 'Report'),
      subtitle: escapeHtml(options.subtitle || ''),
      logoSrc,
      biz,
      partyBlock: '',
      sectionTitle:
        options.sectionTitle !== false
          ? escapeHtml(options.sectionTitle || 'Records')
          : '',
      tableHtml,
      extraHtml: '',
      hideDocTitle: !!options.hideDocTitle,
    });
  }

  function openPrintDocument({
    title,
    subtitle,
    logoSrc,
    biz,
    partyBlock,
    sectionTitle,
    tableHtml,
    extraHtml,
    hideDocTitle,
  }) {
    const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>${title}</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: "Segoe UI", Arial, sans-serif; color: #111; font-size: 12px; margin: 0; padding: 16px; }
    .wrap { max-width: 960px; margin: 0 auto; }
    .header { display: flex; justify-content: space-between; gap: 16px; margin-bottom: 18px; align-items: flex-start; }
    .brand-row { display: flex; align-items: center; gap: 10px; }
    .brand-logo { width: 52px; height: 52px; border-radius: 50%; object-fit: cover; border: 1px solid #ddd; flex-shrink: 0; }
    .brand { font-size: 18px; font-weight: 700; letter-spacing: -0.02em; }
    .meta { color: #555; font-size: 11px; line-height: 1.45; }
    .doc-title { font-size: 16px; font-weight: 700; margin: 0 0 4px; }
    .party {
      background: #f7f7e8; border: 1px solid #e5e5c8; padding: 10px 12px; margin-bottom: 14px;
      display: flex; justify-content: space-between; align-items: flex-start; gap: 16px;
    }
    .party-main { min-width: 0; flex: 1 1 auto; }
    .party-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: #666; margin-bottom: 2px; }
    .party-name { font-weight: 700; font-size: 13px; margin-bottom: 6px; }
    .party-balance { text-align: right; flex: 0 0 auto; min-width: 120px; }
    .party-balance-value { font-weight: 700; font-size: 16px; font-variant-numeric: tabular-nums; }
    .fields { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 14px; margin-top: 8px; }
    .field-label { font-size: 9px; text-transform: uppercase; letter-spacing: 0.05em; color: #666; }
    .field-value { font-weight: 600; font-size: 12px; word-break: break-word; }
    .section-title { font-size: 13px; font-weight: 700; margin: 18px 0 8px; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; }
    th, td { border-bottom: 1px solid #ddd; padding: 7px 6px; vertical-align: top; }
    th { text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: #444; border-bottom: 2px solid #222; }
    td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
    th.check, td.check { width: 28px; text-align: center; vertical-align: middle; }
    .chk {
      display: inline-block; width: 14px; height: 14px;
      border: 1.5px solid #222; border-radius: 2px; vertical-align: middle;
    }
    tfoot td { border-bottom: none; padding-top: 8px; }
    tr.totals td { font-weight: 700; border-top: 1px solid #222; }
    tr.totals td.label { text-align: right; }
    .footer { margin-top: 18px; color: #666; font-size: 10px; display: flex; justify-content: space-between; gap: 12px; }
    .credit {
      margin-top: 16px; padding-top: 10px; border-top: 1px dashed #ccc;
      text-align: center; color: #555; font-size: 10px; line-height: 1.45;
    }
    .credit a { color: #1f7a4d; text-decoration: none; }
    .no-print { text-align: center; margin-bottom: 12px; }
    @media print {
      .no-print { display: none !important; }
      body { padding: 0; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <div class="brand-row">
        ${logoSrc ? `<img class="brand-logo" src="${escapeHtml(logoSrc)}" alt="">` : ''}
        <div>
          <div class="brand">${escapeHtml(biz.company_name || '')}</div>
          <div class="meta">${escapeHtml(biz.company_address || '')}</div>
          <div class="meta">${escapeHtml([biz.company_phone, biz.company_email].filter(Boolean).join(' · '))}</div>
        </div>
      </div>
      <div style="text-align:right">
        ${hideDocTitle ? '' : `<div class="doc-title">${title}</div>`}
        ${subtitle ? `<div class="meta">${subtitle}</div>` : ''}
      </div>
    </div>
    ${partyBlock || ''}
    ${sectionTitle ? `<div class="section-title">${sectionTitle}</div>` : ''}
    ${tableHtml}
    ${extraHtml || ''}
    <div class="footer">
      <span>${escapeHtml(biz.company_name || '')}</span>
      <span>End of report</span>
    </div>
    <div class="credit">
      ${escapeHtml(biz.suite_name || 'Agri Books')} developed by <strong>U.</strong> ${escapeHtml((biz.developer_name || 'U. Technologies').replace(/^U\.\s*/, ''))}
      · <a href="${escapeHtml(biz.developer_url || 'https://udottechnologies.com/')}" target="_blank" rel="noopener noreferrer">udottechnologies.com</a>
    </div>
  </div>
</body>
</html>`;

    // Never use window.open(blob) — on desktop WebView that launches Edge / "Open with".
    // Print from a same-window iframe so the OS printer dialog opens in-app.
    printViaIframe(html);
  }

  function printViaIframe(html) {
    const existing = document.getElementById('erp-print-frame');
    if (existing) existing.remove();

    const frame = document.createElement('iframe');
    frame.id = 'erp-print-frame';
    frame.setAttribute('aria-hidden', 'true');
    // Keep a real size off-screen — display:none / 0×0 breaks print in WebView2.
    frame.style.cssText =
      'position:fixed;left:-10000px;top:0;width:900px;height:1200px;border:0;opacity:0;pointer-events:none;';
    document.body.appendChild(frame);

    let printed = false;
    const runPrint = () => {
      if (printed) return;
      printed = true;
      try {
        const win = frame.contentWindow;
        if (!win) throw new Error('no frame window');
        win.focus();
        win.print();
      } catch (_) {
        alert('Could not open the printer dialog. Check your printer connection.');
      }
      // Keep iframe briefly so the print spooler can read it, then remove.
      setTimeout(() => {
        try {
          frame.remove();
        } catch (_) {
          /* ignore */
        }
      }, 60_000);
    };

    frame.onload = () => setTimeout(runPrint, 250);

    try {
      frame.srcdoc = html;
    } catch (_) {
      try {
        const doc = frame.contentDocument || frame.contentWindow.document;
        doc.open();
        doc.write(html);
        doc.close();
        setTimeout(runPrint, 250);
      } catch (err) {
        frame.remove();
        alert('Could not prepare the print page.');
      }
    }
  }

  global.PrintFiltered = { print, printRows };
})(window);
