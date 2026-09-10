/**
 * Bootstrap nested modals: keep parent visible underneath, fix z-index & orphaned backdrops.
 */
(function (global) {
  function openModals() {
    return [...document.querySelectorAll('.modal.show')];
  }

  function onChildShow(childId) {
    const child = document.getElementById(childId);
    const parents = openModals().filter((m) => m.id !== childId);
    const parent = parents[parents.length - 1];
    if (parent) {
      parent.classList.add('modal-nested-open');
      parent.dataset.nestedBy = childId;
    }
  }

  function onChildHidden(childId) {
    document.querySelectorAll('.modal.modal-nested-open').forEach((parent) => {
      if (!parent.dataset.nestedBy || parent.dataset.nestedBy === childId) {
        parent.classList.remove('modal-nested-open');
        parent.removeAttribute('data-nested-by');
      }
    });
    cleanupOrphans();
  }

  function cleanupOrphans() {
    const showing = openModals();
    if (showing.length) {
      document.body.classList.add('modal-open');
      return;
    }
    document.querySelectorAll('.modal-backdrop').forEach((el) => el.remove());
    document.body.classList.remove('modal-open');
    document.body.style.removeProperty('overflow');
    document.body.style.removeProperty('padding-right');
    document.querySelectorAll('.modal.modal-nested-open').forEach((m) => {
      m.classList.remove('modal-nested-open');
      m.removeAttribute('data-nested-by');
    });
  }

  function bindQuickModal(modalId, onHidden) {
    const el = document.getElementById(modalId);
    if (!el || el.dataset.nestingBound === '1') return;
    el.dataset.nestingBound = '1';
    el.addEventListener('show.bs.modal', () => onChildShow(modalId));
    el.addEventListener('hidden.bs.modal', () => {
      onChildHidden(modalId);
      if (typeof onHidden === 'function') onHidden(modalId);
    });
  }

  global.ErpModalNesting = { onChildShow, onChildHidden, cleanupOrphans, bindQuickModal };

  document.addEventListener('DOMContentLoaded', cleanupOrphans);
  document.addEventListener('hidden.bs.modal', cleanupOrphans);
})(window);
