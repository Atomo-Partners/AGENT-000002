"""Estandarizador de reportes EMIS extraídos con Azure AI Document Intelligence."""

from .catalogo import Catalogo
from .estandarizador import ErrorEntrada, Estandarizador, estandarizar

__all__ = ["Catalogo", "ErrorEntrada", "Estandarizador", "estandarizar"]
