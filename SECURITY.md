# Política de seguridad

Este proyecto es público. **No publiques claves, tokens, certificados, IP privadas, archivos `.storage`, registros con datos personales ni configuraciones completas de Home Assistant.**

## Informar de una vulnerabilidad

Usa la opción **Report a vulnerability** de la pestaña **Security** del repositorio, si está habilitada. Evita abrir una incidencia pública que revele credenciales o métodos de explotación. Nunca adjuntes archivos privados, copias de seguridad ni tokens.

## Verificaciones antes de cada publicación

- Revisión manual del diff completo, incluyendo archivos nuevos y binarios.
- Gitleaks: inspección de posibles credenciales en los commits.
- CodeQL: análisis estático de seguridad de Python.
- Bandit: patrones de riesgo habituales de Python.
- Ruff y pruebas unitarias.
- HACS y hassfest: validación de integridad e integración con Home Assistant.
- Dependabot: actualización de dependencias de GitHub Actions.

Los análisis automáticos **no garantizan ausencia de vulnerabilidades**. Cada versión requiere además validación funcional en Home Assistant y revisión de su acceso únicamente a `boe.es`.

## Datos locales

Las preferencias de tarifa y el histórico local se guardan en Home Assistant. No se utilizan servicios de telemetría propios.
