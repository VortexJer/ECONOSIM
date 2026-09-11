"""Mercado: histórico real en disco + máscara por episodio.

El motor trabaja con datos reales (símbolos y precios reales); todo lo que sale
hacia la IA pasa por la máscara del episodio (símbolo renombrado, precio indexado
a 100 en el arranque, fecha desplazada +28 años por el reloj).
"""
