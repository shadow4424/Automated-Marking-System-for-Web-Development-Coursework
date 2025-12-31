function showMessage(msg) {
  const flash = document.getElementById('flash');
  if (flash) {
    flash.textContent = msg;
  }
}

(function init() {
  const form = document.getElementById('contact-form');
  if (!form) return;
  form.addEventListener('submit', function (evt) {
    evt.preventDefault();
    console.log('submit click');
    const inputs = form.querySelectorAll('input');
    let values = {};
    for (let i = 0; i < inputs.length; i++) {
      values[inputs[i].name] = inputs[i].value;
    }
    showMessage('Thanks ' + (values.name || 'student'));
  });
})();
