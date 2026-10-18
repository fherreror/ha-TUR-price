# TUR Gas (BOE) — Home Assistant

Integración personalizada para la tarifa de último recurso (TUR) del gas natural en España, que obtiene los precios regulados del [BOE](https://www.boe.es/) y, **opcionalmente**, el factor oficial de conversión de [Enagás](https://www.enagas.es/es/gestion-tecnica-sistema/energy-data/informacion-comercial/factor-conversion-facturacion/).

**Estado:** versión estable 0.1.0 (primera publicación). No sustituye a la factura de la comercializadora ni incluye impuestos, alquiler de contador ni prorrateos del término fijo.

## Sensores del BOE

- Precio variable, en €/kWh, con clase de estado `measurement`.
- Término fijo, en €/mes.
- Fecha de vigencia de la resolución.
- Última comprobación de los sumarios del BOE.

La modalidad TUR.1–TUR.11 es configurable. Las publicaciones y sus periodos de vigencia se conservan localmente; en caso de error no se sobrescriben las últimas tarifas válidas.

## Conversión oficial de Enagás (opcional)

En **Configurar > Opciones** de la integración se puede seleccionar:
- **Zona de suministro:** lista oficial de municipios y zonas de distribución de Enagás.
- **Presión de suministro:** valor en mbar, tal como corresponda al suministro.
- **Tipo de lectura:** mensual o bimestral.

Si se activa esta opción se añaden:
- **Factor conversión Enagás**, en kWh/m³, con la fecha a la que corresponde.
- **Precio variable por volumen**, en €/m³, calculado como precio BOE (€/kWh) × factor Enagás (kWh/m³).

El factor se consulta para la fecha reciente más próxima disponible, y se actualiza en la actualización de medianoche de la integración. Si falla Enagás se mantiene el último valor validado, pero cuando tiene más de 14 días **el precio por volumen deja de mostrarse como actual**. La fuente, zona, presión, tipo de lectura y fecha quedan en los atributos.

**Aviso técnico:** la web de Enagás ofrece estos datos mediante servicios JSON usados por su propia página, pero no se ha localizado una API pública estable documentada. Una modificación de su web podría requerir actualizar el cliente. Los errores se registran sin exponer direcciones privadas, claves ni datos del suministro.

## Ejemplo contrastado

Factura: Madrid 2 (`28MA2`), 22 mbar, lectura bimestral al 22/09/2026. Enagás proporciona un factor preciso de **10,6110152 kWh/m³**, que coincide con los coeficientes reflejados en la factura. Este valor **no debe utilizarse como constante para otros periodos**.

## Desarrollo y seguridad

- `tests/test_boe.py`, `tests/test_recovery.py`, `tests/test_coordinator.py` y `tests/test_enagas.py`: pruebas de regresión.
- Validación automática mediante Ruff, unittest, Bandit, Gitleaks, CodeQL, HACS y hassfest.
- Descargas solo de fuentes oficiales conocidas, con límites de tamaño y validación estricta de los datos.
- No se requieren credenciales de Enagás ni del BOE.
