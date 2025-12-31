function setup() {
  const form = document.querySelector('form');
  form.addEventListener('submit', (evt) => {
    evt.preventDefault();
    console.log('submitted');
  });
}

setup();
