document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("transvid-form");
  const progressContainer = document.getElementById("progress-container");
  const progressText = document.getElementById("progress-text");
  const progressFill = document.getElementById("progress-fill");
  const log = document.getElementById("log");

  form.addEventListener("submit", (e) => {
    e.preventDefault();

    // Afficher la barre de progression et le log
    progressContainer.style.display = "block";
    log.style.display = "block";
    progressFill.style.width = "0%";
    log.innerHTML = "";

    const steps = [
      {text: "Téléchargement de la vidéo...", percent: 10},
      {text: "Transcription audio...", percent: 30},
      {text: "Traduction des segments...", percent: 60},
      {text: "Génération de la voix française...", percent: 80},
      {text: "Fusion audio/vidéo...", percent: 95},
      {text: "Terminé !", percent: 100}
    ];

    // Simuler progression côté client pour plus de feedback
    let stepIndex = 0;
    const interval = setInterval(() => {
      if(stepIndex >= steps.length) {
        clearInterval(interval);
        return;
      }
      const step = steps[stepIndex];
      progressText.textContent = step.text;
      progressFill.style.width = step.percent + "%";
      log.innerHTML += "🔹 " + step.text + "<br>";
      log.scrollTop = log.scrollHeight;
      stepIndex++;
    }, 1000); // chaque étape toutes les 1 sec pour visualiser (simulation)
    
    // Envoyer le formulaire via fetch
    fetch("/", {
      method: "POST",
      body: new FormData(form)
    })
    .then(res => res.text())
    .then(html => {
      clearInterval(interval);
      progressFill.style.width = "100%";
      progressText.textContent = "✅ Tout est terminé !";
      log.innerHTML += "🎉 Toutes les étapes sont terminées.<br>";
      document.body.innerHTML = html; // remplace la page par le rendu final Flask
    })
    .catch(err => {
      clearInterval(interval);
      progressText.textContent = "⚠️ Erreur";
      log.innerHTML += "❌ " + err;
    });
  });
});
