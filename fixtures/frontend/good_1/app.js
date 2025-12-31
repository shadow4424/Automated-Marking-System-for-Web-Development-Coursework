function init() {
  const el = document.querySelector('body');
  el.addEventListener('click', () => {
    console.log('clicked');
  });
}

init();
