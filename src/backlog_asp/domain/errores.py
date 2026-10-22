"""Errores de negocio. La capa de presentación los convierte en respuestas HTTP con su código."""


class ErrorNegocio(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class LoadError(ErrorNegocio):
    """Archivo cargado con problemas (formato, columnas, orden)."""


class GestionError(ErrorNegocio):
    """Cambio de gestión no permitido o con datos no válidos."""


class MiamiError(ErrorNegocio):
    """Compra en Miami no válida."""
