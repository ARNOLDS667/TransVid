document.addEventListener("DOMContentLoaded", () => {
  // This script is a fallback for older UI; the main app uses the socket-based index.html handlers.
  // We keep it lightweight: add a handler to the purge button if present.
  const purgeBtn = document.getElementById('purgeBtn');
  const toast = document.getElementById('toast');
  if(purgeBtn){
    purgeBtn.addEventListener('click', ()=>{
      purgeBtn.disabled = true;
      fetch('/purge_temp', {method:'POST'})
        .then(r=> r.text())
        .then(txt=>{
          showToast(txt);
        })
        .catch(err=> showToast('Erreur purge: '+err, true))
        .finally(()=> purgeBtn.disabled = false);
  
    });
  }

  function showToast(message, isError=false){
    if(!toast) return;
    toast.textContent = message;
    toast.style.background = isError ? '#c0392b' : '#2d7a2d';
    toast.style.display = 'block';
    setTimeout(()=> toast.style.display = 'none', 4000);
  }
});
