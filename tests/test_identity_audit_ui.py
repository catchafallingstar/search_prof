import ast
from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest


class IdentityAuditUITests(unittest.TestCase):
    def test_staff_renders_ten_links_and_snippet_warning(self):
        path=Path(__file__).resolve().parents[1]/'pages'/'5_Radar_control.py'
        source=path.read_text()
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='render_identity_context')
        function=ast.get_source_segment(source,node)
        script="import streamlit as st\nfrom ingestion.verification_audit import safe_source_link\n"+function+"\n"
        script+="render_identity_context({'identity_search_audit': {'outcome':'UNVERIFIED', 'results':[{'url':f'https://www.linkedin.com/in/test{i}', 'title':f'Result {i}', 'snippet':'Example PhD candidate', 'snippet_hint':'Snippet only; role not confirmed'} for i in range(20)]}})"
        app=AppTest.from_string(script).run(timeout=10)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.get('link_button')),10)
        self.assertEqual(sum('Snippet only; role not confirmed' in warning.value for warning in app.warning),10)
