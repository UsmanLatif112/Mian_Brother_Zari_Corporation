(function () {
  const cart = {};
  const tbody = document.querySelector('#cart-table tbody');
  const subtotalEl = document.getElementById('subtotal');
  const csrf = document.querySelector('meta[name=csrf-token]')?.content;

  function render() {
    tbody.innerHTML = '';
    let subtotal = 0;
    Object.values(cart).forEach(line => {
      subtotal += line.qty * line.price;
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${line.name}</td><td><input type="number" min="1" value="${line.qty}" data-id="${line.id}" class="form-control form-control-sm qty-input" style="width:70px"></td><td>${(line.qty*line.price).toFixed(2)}</td><td><button class="btn btn-sm btn-link text-danger remove" data-id="${line.id}">&times;</button></td>`;
      tbody.appendChild(tr);
    });
    subtotalEl.textContent = subtotal.toFixed(2);
  }

  function addProduct(id, name, price) {
    if (!cart[id]) cart[id] = { id, name, price: Number(price), qty: 1 };
    else cart[id].qty += 1;
    render();
  }

  document.getElementById('product-grid')?.addEventListener('click', e => {
    const btn = e.target.closest('.product-tile');
    if (!btn) return;
    addProduct(btn.dataset.id, btn.dataset.name, btn.dataset.price);
  });

  document.getElementById('product-search')?.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    const q = e.target.value.trim().toLowerCase();
    const tile = [...document.querySelectorAll('.product-tile')].find(t => t.dataset.barcode === q || t.dataset.name.toLowerCase().includes(q));
    if (tile) addProduct(tile.dataset.id, tile.dataset.name, tile.dataset.price);
    e.target.value = '';
  });

  tbody?.addEventListener('input', e => {
    if (!e.target.classList.contains('qty-input')) return;
    cart[e.target.dataset.id].qty = Number(e.target.value) || 1;
    render();
  });
  tbody?.addEventListener('click', e => {
    if (!e.target.classList.contains('remove')) return;
    delete cart[e.target.dataset.id];
    render();
  });

  document.getElementById('apply-customer')?.addEventListener('click', () => {
    const sel = document.getElementById('customer-select');
    document.getElementById('customer-id').value = sel.value;
    document.getElementById('customer-label').textContent = sel.options[sel.selectedIndex].text;
  });

  document.getElementById('save-customer')?.addEventListener('click', async () => {
    const res = await fetch('/api/customers/quick', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.CSRF_TOKEN },
      body: JSON.stringify({ name: document.getElementById('nc-name').value, phone: document.getElementById('nc-phone').value })
    });
    const data = await res.json();
    if (data.ok) {
      const sel = document.getElementById('customer-select');
      const opt = document.createElement('option');
      opt.value = data.id; opt.textContent = data.name; sel.appendChild(opt); sel.value = data.id;
    }
  });

  document.getElementById('save-product')?.addEventListener('click', async () => {
    const res = await fetch('/api/products/quick', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.CSRF_TOKEN },
      body: JSON.stringify({ name: document.getElementById('np-name').value, sale_price: document.getElementById('np-price').value })
    });
    const data = await res.json();
    if (data.ok) location.reload();
  });

  async function completeSale() {
    const items = Object.values(cart).map(l => ({ product_id: Number(l.id), quantity: l.qty, unit_price: l.price }));
    if (!items.length) return alert('Cart is empty');
    const res = await fetch('/sales/pos/submit', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.CSRF_TOKEN },
      body: JSON.stringify({
        items,
        customer_id: document.getElementById('customer-id').value || null,
        payment_method: document.getElementById('payment-method').value
      })
    });
    const data = await res.json();
    if (data.ok) { alert('Sale complete: ' + data.invoice_no); location.href = '/sales/' + data.sale_id + '/invoice'; }
    else alert(data.error || 'Sale failed');
  }

  document.getElementById('complete-sale')?.addEventListener('click', completeSale);
  document.addEventListener('keydown', e => { if (e.key === 'F2') { e.preventDefault(); completeSale(); } });
})();
