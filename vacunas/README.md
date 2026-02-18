# Vacunas — Scripts de corrección de base de datos

> **CONFIDENCIAL — USO PERSONAL**
> Historial de correcciones retroactivas aplicadas sobre Supabase.
> Última actualización: febrero 2026

---

## ¿Qué es una vacuna?

Un script Python que corrige datos en Supabase como consecuencia de un bug en el sistema de análisis. Se ejecuta una sola vez, manualmente, en local. Nunca se ejecuta desde GitHub Actions.

**Convención de nombres:** `YYYY-MM-DD_nombre_descriptivo.py`

**Carpetas:**
- `aplicadas/` — vacunas ya ejecutadas en producción. No volver a ejecutar salvo indicación explícita.

---

## Registro de vacunas aplicadas

| Fecha | Archivo | Bug que corrige | Filas afectadas | Aplicada por | Reversible |
|-------|---------|----------------|-----------------|--------------|------------|
| 2026-02-15 | `aplicadas/2026-02-15_triple_counting_b14_b18_b19.py` | Triple-counting B14+B18d+B19b: score inflado hasta +95 pts en compras grandes únicas | ~alertas con B14+B18d+B19b simultáneos | manual | NO (recálculo in-place) |
| 2026-02-15 | `aplicadas/2026-02-15_resolver_clob_api.py` | Market resolver con endpoint Gamma API roto: 94 alertas con outcome basado en mercado Biden 2020 | 94 alertas | manual | Sí (guardar backup de outcome antes) |
| 2026-02-15 | `aplicadas/2026-02-15_precios_direccion_no.py` | Inversión incorrecta de precios en trades dirección NO: filtros B07/B25 evaluaban complemento del precio real | Trades con direction=NO en ventana afectada | manual | NO |
| 2026-02-16 | `aplicadas/2026-02-16_rate_limiting_deep_scan.py` | Rate limiting deep scan: scans parciales por errores 429 silenciosos — mercados sin analizar | Scans con status=error o incompletos | manual | NO aplica |
| 2026-02-16 | `aplicadas/2026-02-16_dashboard_precio_entrada.py` | Dashboard: precio de entrada del primer trade disponible en vez del wallet principal + estrellas sobre score_raw | Todas las alertas en dashboard | manual | NO aplica (dashboard se regenera) |
| 2026-02-17 | `aplicadas/2026-02-17_falsa_confluencia_infra.py` | Falsa confluencia por senders de infraestructura (Relay Solver, wrapped collateral) detectados como C03d/C07 | Alertas con C03d/C07 de senders infra | manual | Parcial (ver script) |
| 2026-02-17 | `aplicadas/2026-02-17_falsos_positivos_ventana_corta.py` | Falsos positivos por dependencia de ventana de 35 min: W04/W05/W09/B20/B23/B28/N06 disparados sobre wallets con historial real en PM | 342 alertas | manual | NO |
| 2026-02-18 | `aplicadas/2026-02-18_eliminar_alertas_cero_estrellas.py` | Eliminar ~375 alertas con star_level=0 acumuladas antes del gate 0★ en main.py | ~375 alertas + dependencias FK | manual | NO |
| 2026-02-18 | `aplicadas/2026-02-18_cross_scan_dedup_score_inconsistency.py` | Cross-scan dedup actualizaba `score` sin actualizar `score_raw`/`star_level`: 10 alertas con campos inconsistentes | 10 alertas | manual | NO |
| 2026-02-18 | `2026-02-18_star_level_downgrade_amount_validation.py` | Alertas históricas con star_level inflado por sistema antiguo (sin requisito de importe mínimo): 4★ requiere $5k, 5★ requiere $10k | 4 alertas (649, 845, 1066, 1468) | **PENDIENTE** | NO |

---

## Cómo crear una nueva vacuna

1. Copia `template.py` con el nombre `YYYY-MM-DD_descripcion_breve.py`
2. Rellena la cabecera (todos los campos)
3. Implementa las tres secciones: DIAGNÓSTICO → CORRECCIÓN → VERIFICACIÓN
4. **Siempre ejecutar en modo dry-run primero** (`DRY_RUN = True`)
5. Confirmar resultados del diagnóstico antes de activar corrección
6. Una vez ejecutada, mover a `aplicadas/` y añadir fila a esta tabla
7. Comentar en el script la fecha de ejecución y el resultado (filas modificadas)

---

## Reglas de seguridad

- Nunca ejecutar en CI/CD. Solo local con credenciales propias.
- El script no debe modificar nada sin confirmación explícita del operador (`input("Continuar? [s/N]")`).
- Si el script falla a mitad, debe poder ejecutarse de nuevo sin duplicar correcciones (idempotente).
- Documentar el resultado real (filas afectadas) en el comentario de ejecución del script.
