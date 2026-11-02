# Repository Guidelines

## Objetivo
Crear una herramienta local para descargar canciones y playlists mediante enlaces de Spotify y SoundCloud, en MP3 o WAV cuando sea viable. Priorizar uso personal, automatización e integración con Piloto DJ mediante CLI, sin interfaz web.

Permitir fuentes alternativas para canciones identificadas en Spotify; registrar la procedencia real y rechazar coincidencias ambiguas o versiones incorrectas. No prometer extracción directa ni calidad sin pérdidas. Convertir audio comprimido a WAV no mejora su calidad. Utilizar contenido propio o autorizado, sin eludir DRM ni controles de acceso.

## Estructura, estilo y comandos
El proyecto usa Python 3.11–3.15, setuptools, spotDL, yt-dlp y ytmusicapi. La CLI vive en `ytscloud.py`. Las pruebas están en `tests/` y CI en `.github/workflows/ci.yml`. Documentar en README la instalación, ejecución y pruebas conforme se implementen.

Ejecutar `python -m unittest discover -s tests -v` y `python -m pip check` en el entorno del proyecto. FFmpeg y ffprobe son necesarios para las pruebas de audio; las llamadas remotas se simulan.

Elegir la solución mínima; reutilizar herramientas mantenidas antes de crear descargadores propios. Separar responsabilidades cuando sea necesario. Adoptar la indentación, nomenclatura y formateador del lenguaje elegido; evitar dependencias y abstracciones innecesarias.

## Automatización y configuración
Permitir ejecuciones sin interacción, resultados estructurados y códigos de salida claros. Confirmar el contrato de Piloto DJ antes de integrar. Evitar duplicados, sobrescrituras y borrados implícitos; limitar reintentos y conservar archivos completos. Mantener credenciales, tokens y audio fuera del control de versiones.

Consultar como referencias spotDL/spotify-downloader, camathieu/soundcloudsync, victorespnt-zz/SouncloudTrackDownloader, thomas0barand/utimate-soundcloud-downloader, SpotDown, SpotSaver y SCloudDownTech. Verificar capacidades, mantenimiento y licencias antes de reutilizar código.

## Agentes, modelos y cuotas
Buscar el mejor resultado con el menor consumo total. Usar un agente; delegar en Orca únicamente tareas independientes, sin duplicar trabajo.

Preferir Codex GPT-6.1 Terra (medium). Usar GPT-6.1 Sol (medium) para problemas complejos o cambios entre módulos; subir Sol a high solo ante una dificultad concreta.

Comprobar cuotas de Codex antes de nuevas tareas. Si queda menos del 30 % en cinco horas o semanal, usar GitHub Copilot CLI (Auto) en Orca. Si tampoco alcanza, detener lo pendiente e indicar qué falta y cuándo se renueva. Si cuotas, renovaciones o modelos no pueden verificarse, declararlo sin inventar datos ni sustituir modelos silenciosamente.

## Pruebas y contribuciones
Definir criterios de aceptación, revisar cambios y ejecutar pruebas pertinentes. Cubrir enlaces inválidos, duplicados, fallos de red, coincidencias y audio reproducible. Usar muestras autorizadas y pruebas automatizadas independientes de servicios externos.

Usar mensajes de commit breves y descriptivos. En PR y entregas, resumir cambios, comprobaciones, limitaciones y agente/modelo realmente utilizados.
