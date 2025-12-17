function init(){
  const el=document.querySelector("body");
  el.addEventListener("click", () => { fetch("/api"); });
}
