# TUR Gas BOE — Home Assistant

Integración personalizada de Home Assistant para obtener los precios regulados de la tarifa de último recurso (TUR) del gas natural en España desde fuentes oficiales.

> **Estado: en desarrollo.** Todavía no existe una versión instalable validada. No utilizar como fuente de cálculo de costes hasta verificar el parser contra XML reales del BOE.

## Diseño previsto

- Consulta del índice oficial del BOE; identificación de resoluciones por título y contenido, **sin depender del nombre del ministerio o la dirección general**.
- Extracción y validación estrictas de las tablas de precios, unidades y fechas de vigencia.
- Selección de modalidad TUR en el asistente de configuración (TUR.1 por defecto).
- Sensores del término variable en €/kWh y término fijo en €/mes, con estadísticas de Home Assistant para el precio variable.
- Persistencia local de los precios históricos y de la referencia exacta del BOE; conservación del último valor validado en caso de error.
- Actualización automática y revisión de correcciones publicadas.
- Icono de integración con recursos `brand/` para versiones compatibles de Home Assistant.
- Diseñada para utilizarse con un sensor existente de consumo de gas en kWh y estimar el coste variable real día a día, **sin prorrateos de factura**.

## Siguientes verificaciones

1. Probar los formatos reales de la API de sumarios y los XML de las últimas resoluciones.
2. Ajustar el lector de tablas, encabezados y fechas, y ampliar las pruebas de regresión.
3. Validar instalación, configuración, sensores, icono y estadísticas en Home Assistant.
4. Preparar una versión beta etiquetada y documentación de HACS.

Fuente prevista: https://www.boe.es/datosabiertos/api/api.php
