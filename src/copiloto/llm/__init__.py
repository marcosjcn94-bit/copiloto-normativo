"""Camada de provedor de LLM (§3.2 do briefing).

Uma interface, várias implementações. O agente nunca importa um provedor
concreto: recebe um `ProvedorLLM` e não sabe quem atende.
"""
