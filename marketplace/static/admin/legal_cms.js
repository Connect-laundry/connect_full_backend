(function () {
  function updateCounter() {
    var field = document.getElementById('id_content_markdown');
    var label = document.getElementById('legal-content-counter');
    if (!field || !label) return;
    label.textContent = field.value.length + ' characters';
  }

  document.addEventListener('DOMContentLoaded', function () {
    var field = document.getElementById('id_content_markdown');
    if (!field) return;
    var label = document.createElement('div');
    label.id = 'legal-content-counter';
    label.style.marginTop = '6px';
    label.style.color = '#6b7280';
    label.style.fontSize = '12px';
    field.parentNode.appendChild(label);
    field.addEventListener('input', updateCounter);
    updateCounter();
  });
})();
